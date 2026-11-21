use reqwest::{Client, Response, Url};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{
    fs::{self, File},
    io::{Read, Write},
    path::PathBuf,
    sync::{
        Arc, Mutex,
        atomic::{AtomicBool, Ordering},
    },
    time::Duration,
};
use tauri::{AppHandle, Manager, State, ipc::Channel};

const RELEASE_FEED: &str =
    "https://api.github.com/repos/Cass-ette/claude-cchp-manager/releases?per_page=20";

#[derive(Deserialize)]
struct Release {
    draft: bool,
    prerelease: bool,
    published_at: Option<String>,
    assets: Vec<ReleaseAsset>,
}

#[derive(Deserialize)]
struct ReleaseAsset {
    name: String,
    browser_download_url: String,
}

fn trusted_release_url(address: &str) -> bool {
    Url::parse(address).is_ok_and(|url| {
        url.scheme() == "https"
            && url.host_str() == Some("github.com")
            && url.username().is_empty()
            && url.password().is_none()
            && url.port().is_none()
            && url.query().is_none()
            && url.fragment().is_none()
            && url
                .path()
                .starts_with("/Cass-ette/claude-cchp-manager/releases/download/")
    })
}

fn select_manifest(mut releases: Vec<Release>) -> Option<(String, &'static str)> {
    releases.retain(|r| !r.draft && r.published_at.is_some());
    releases.sort_by(|a, b| b.published_at.cmp(&a.published_at));
    for release in releases {
        let assets: Vec<_> = release
            .assets
            .into_iter()
            .filter(|a| a.name == "update.json")
            .collect();
        if assets.len() == 1
            && trusted_release_url(&assets[0].browser_download_url)
            && Url::parse(&assets[0].browser_download_url)
                .ok()?
                .path()
                .ends_with("/update.json")
        {
            return Some((
                assets[0].browser_download_url.clone(),
                if release.prerelease {
                    "preview"
                } else {
                    "stable"
                },
            ));
        }
    }
    None
}

#[derive(Deserialize)]
struct Manifest {
    schema: u32,
    notes: String,
    packages: Vec<Package>,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
pub(crate) struct Package {
    platform: String,
    arch: String,
    channel: String,
    version: String,
    url: String,
    size: u64,
    sha256: String,
}

#[derive(Serialize)]
pub(crate) struct Available {
    package: Package,
    notes: String,
}

struct Staged {
    package: Package,
    path: PathBuf,
    keep: bool,
}

impl Drop for Staged {
    fn drop(&mut self) {
        if !self.keep {
            let _ = fs::remove_file(&self.path);
        }
    }
}

#[derive(Default)]
pub(crate) struct UpdateState {
    active: Mutex<Option<Arc<AtomicBool>>>,
    staged: Mutex<Option<Staged>>,
}

struct Transfer<'a>(&'a Mutex<Option<Arc<AtomicBool>>>);

impl Drop for Transfer<'_> {
    fn drop(&mut self) {
        if let Ok(mut active) = self.0.lock() {
            *active = None;
        }
    }
}

fn version(value: &str) -> Option<[u32; 3]> {
    let parts: Vec<_> = value.split('.').collect();
    if parts.len() != 3 || parts.iter().any(|p| !p.bytes().all(|c| c.is_ascii_digit())) {
        return None;
    }
    Some([
        parts[0].parse().ok()?,
        parts[1].parse().ok()?,
        parts[2].parse().ok()?,
    ])
}

fn client() -> Result<Client, String> {
    Client::builder()
        .https_only(true)
        .connect_timeout(Duration::from_secs(15))
        .read_timeout(Duration::from_secs(35))
        .user_agent("Agent-Workbench")
        .build()
        .map_err(|_| "无法初始化更新连接。".into())
}

async fn fetch(client: &Client, url: &str) -> Result<Response, String> {
    client
        .get(url)
        .send()
        .await
        .and_then(Response::error_for_status)
        .map_err(|error| {
            format!(
                "无法从 GitHub 读取更新。{}",
                error
                    .status()
                    .map(|s| format!("（HTTP {}）", s.as_u16()))
                    .unwrap_or_default()
            )
        })
}

async fn read_document(client: &Client, url: &str) -> Result<Vec<u8>, String> {
    let mut response = fetch(client, url).await?;
    let mut bytes = Vec::new();
    while let Some(chunk) = response.chunk().await.map_err(|_| "读取发布清单失败。")? {
        if bytes.len() + chunk.len() > 512 * 1024 {
            return Err("发布清单过大。".into());
        }
        bytes.extend_from_slice(&chunk);
    }
    Ok(bytes)
}

async fn lookup(client: &Client, current: &str) -> Result<Option<Available>, String> {
    let releases: Vec<Release> =
        serde_json::from_slice(&read_document(client, RELEASE_FEED).await?)
            .map_err(|_| "无法识别发布列表。")?;
    let (url, channel) =
        select_manifest(releases).ok_or("发布页暂时没有完整更新包，可以稍后重试或手动下载。")?;
    let bytes = read_document(client, &url).await?;
    let manifest: Manifest = serde_json::from_slice(&bytes).map_err(|_| "无法识别发布清单。")?;
    if manifest.schema != 1 {
        return Err("无法识别发布清单版本。".into());
    }
    let arch = match std::env::consts::ARCH {
        "x86_64" => "x64",
        "aarch64" => "arm64",
        _ => return Err("当前系统架构暂无安装包。".into()),
    };
    let mut packages = manifest
        .packages
        .into_iter()
        .filter(|p| p.platform == "windows" && p.arch == arch && p.channel == channel);
    let package = packages
        .next()
        .ok_or("发布清单没有适用于这台电脑的安装包。")?;
    if packages.next().is_some() {
        return Err("发布清单有重复的安装包。".into());
    }
    let url = Url::parse(&package.url).map_err(|_| "安装包地址无效。")?;
    if !trusted_release_url(&package.url)
        || url.scheme() != "https"
        || url.host_str() != Some("github.com")
        || !url.username().is_empty()
        || url.port().is_some()
        || url.query().is_some()
        || url.fragment().is_some()
        || !url
            .path()
            .starts_with("/Cass-ette/claude-cchp-manager/releases/download/")
        || !url.path().ends_with("-setup.exe")
        || package.size == 0
        || package.size > 1_073_741_824
        || package.sha256.len() != 64
        || !package
            .sha256
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
    {
        return Err("安装包信息无效。".into());
    }
    let new = version(&package.version).ok_or("新版的版本号无效。")?;
    let old = version(current).ok_or("当前版本号无效。")?;
    Ok((new > old).then_some(Available {
        package,
        notes: manifest.notes,
    }))
}

#[tauri::command]
pub(crate) async fn check_workbench_update(app: AppHandle) -> Result<Option<Available>, String> {
    lookup(&client()?, &app.package_info().version.to_string()).await
}

#[tauri::command]
pub(crate) async fn download_workbench_update(
    app: AppHandle,
    state: State<'_, UpdateState>,
    expected: Package,
    progress: Channel<u8>,
) -> Result<(), String> {
    let cancelled = Arc::new(AtomicBool::new(false));
    {
        let mut active = state.active.lock().map_err(|_| "更新状态不可用。")?;
        if active.is_some() {
            return Err("已有下载正在运行。".into());
        }
        *active = Some(cancelled.clone());
    }
    let _transfer = Transfer(&state.active);
    state.staged.lock().map_err(|_| "更新状态不可用。")?.take();
    let client = client()?;
    let current = app.package_info().version.to_string();
    let latest = lookup(&client, &current).await?;
    if latest.as_ref().map(|item| &item.package) != Some(&expected) {
        return Err("可用版本已变化，请重新检查更新。".into());
    }
    check_cancel(&cancelled)?;
    let directory = app
        .path()
        .app_cache_dir()
        .map_err(|e| e.to_string())?
        .join("updates");
    fs::create_dir_all(&directory).map_err(|_| "无法创建更新目录。")?;
    let path = directory.join(format!(
        "workbench-{}-{}-setup.exe",
        expected.version,
        uuid::Uuid::new_v4()
    ));
    let mut response = fetch(&client, &expected.url).await?;
    let mut file = File::create_new(&path).map_err(|_| "无法创建安装包文件。")?;
    let staged = Staged {
        package: expected,
        path,
        keep: false,
    };
    let result = write_download(
        &mut response,
        &mut file,
        &staged.package,
        &cancelled,
        &progress,
    )
    .await;
    drop(file);
    result?;
    check_cancel(&cancelled)?;
    *state.staged.lock().map_err(|_| "更新状态不可用。")? = Some(staged);
    Ok(())
}

async fn write_download(
    response: &mut Response,
    file: &mut File,
    package: &Package,
    cancelled: &AtomicBool,
    progress: &Channel<u8>,
) -> Result<(), String> {
    let mut count = 0;
    let mut hash = Sha256::new();
    let mut reported = 0;
    loop {
        check_cancel(cancelled)?;
        let bytes = response
            .chunk()
            .await
            .map_err(|_| "安装包下载中断，请重试。")?;
        let Some(bytes) = bytes else { break };
        check_cancel(cancelled)?;
        count += bytes.len() as u64;
        if count > package.size {
            return Err("安装包大小与发布清单不符。".into());
        }
        file.write_all(&bytes).map_err(|_| "无法写入安装包。")?;
        hash.update(&bytes);
        let percent = (100 * count / package.size).min(99) as u8;
        if percent > reported {
            let _ = progress.send(percent);
            reported = percent;
        }
    }
    if count != package.size || format!("{:x}", hash.finalize()) != package.sha256 {
        return Err("安装包校验失败，请重新下载。".into());
    }
    let _ = progress.send(100);
    Ok(())
}

fn check_cancel(cancelled: &AtomicBool) -> Result<(), String> {
    if cancelled.load(Ordering::Relaxed) {
        Err("下载已取消。".into())
    } else {
        Ok(())
    }
}

#[tauri::command]
pub(crate) fn cancel_workbench_update(state: State<'_, UpdateState>) {
    if let Ok(active) = state.active.lock()
        && let Some(cancelled) = active.as_ref()
    {
        cancelled.store(true, Ordering::Relaxed);
    }
}

#[derive(Serialize)]
pub(crate) struct InstallError {
    message: String,
    redownload: bool,
}

impl From<&str> for InstallError {
    fn from(message: &str) -> Self {
        Self {
            message: message.into(),
            redownload: false,
        }
    }
}

impl From<String> for InstallError {
    fn from(message: String) -> Self {
        Self {
            message,
            redownload: false,
        }
    }
}

fn verify_staged(staged: &Staged) -> bool {
    let Ok(file) = File::open(&staged.path) else {
        return false;
    };
    let mut hash = Sha256::new();
    std::io::copy(&mut file.take(staged.package.size + 1), &mut hash).is_ok_and(|size| {
        size == staged.package.size && format!("{:x}", hash.finalize()) == staged.package.sha256
    })
}

#[tauri::command]
pub(crate) fn install_workbench_update(
    app: AppHandle,
    state: State<'_, UpdateState>,
    workbench: State<'_, super::AppState>,
    expected: Package,
) -> Result<(), InstallError> {
    if !cfg!(target_os = "windows") {
        return Err("安装仅在 Windows 上可用。".into());
    }
    let mut closing = workbench
        .closing_for_update
        .lock()
        .map_err(|_| "终端状态不可用。")?;
    let running = workbench
        .terminals
        .lock()
        .map_err(|_| "终端状态不可用。")?
        .len();
    if running != 0 {
        return Err(InstallError {
            message: format!("还有 {running} 个终端正在运行。请在任务完成后回来安装。"),
            redownload: false,
        });
    }
    let mut state = state.staged.lock().map_err(|_| "更新状态不可用。")?;
    let Some(staged) = state.as_mut().filter(|s| s.package == expected) else {
        return Err(InstallError {
            message: "请先下载选中的更新。".into(),
            redownload: true,
        });
    };
    if !verify_staged(staged) {
        state.take();
        return Err(InstallError {
            message: "安装包已丢失或校验失败，请重新下载。".into(),
            redownload: true,
        });
    }
    std::process::Command::new(&staged.path)
        .spawn()
        .map_err(|_| "无法打开 Windows 安装程序。")?;
    staged.keep = true;
    *closing = true;
    drop(state);
    drop(closing);
    app.exit(0);
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn discovers_published_previews_and_rejects_untrusted_manifests() {
        fn release(date: &str, preview: bool, draft: bool, url: &str) -> Release {
            Release {
                draft,
                prerelease: preview,
                published_at: Some(date.into()),
                assets: vec![ReleaseAsset {
                    name: "update.json".into(),
                    browser_download_url: url.into(),
                }],
            }
        }
        let url =
            "https://github.com/Cass-ette/claude-cchp-manager/releases/download/test/update.json";
        assert_eq!(
            select_manifest(vec![
                release("2026-01-01", false, false, url),
                release("2026-02-01", true, false, url)
            ]),
            Some((url.into(), "preview"))
        );
        assert_eq!(
            select_manifest(vec![
                release("2026-01-01", false, false, url),
                release("2026-02-01", true, true, url)
            ]),
            Some((url.into(), "stable"))
        );
        for bad in [
            url.replace("github.com", "example.com"),
            url.replace("Cass-ette/", "other/"),
            format!("{url}?extra=1"),
        ] {
            assert!(select_manifest(vec![release("2026-02-01", true, false, &bad)]).is_none());
        }
        assert!(select_manifest(vec![]).is_none());
    }

    #[test]
    fn rechecks_the_staged_installer_before_launch() {
        let bytes = b"installer fixture";
        let staged = Staged {
            package: Package {
                platform: "windows".into(),
                arch: "x64".into(),
                channel: "stable".into(),
                version: "1.2.3".into(),
                url: String::new(),
                size: bytes.len() as u64,
                sha256: format!("{:x}", Sha256::digest(bytes)),
            },
            path: std::env::temp_dir().join(format!("workbench-test-{}", uuid::Uuid::new_v4())),
            keep: false,
        };
        assert!(!verify_staged(&staged));
        fs::write(&staged.path, bytes).unwrap();
        assert!(verify_staged(&staged));
        fs::write(&staged.path, b"modified contents").unwrap();
        assert!(!verify_staged(&staged));
        fs::write(&staged.path, [bytes.as_slice(), b" trailing data"].concat()).unwrap();
        assert!(!verify_staged(&staged));
    }
}
