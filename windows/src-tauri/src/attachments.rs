use std::{
    fs::{self, OpenOptions},
    io::Write,
    path::Path,
};
use tauri::Manager;

fn save_png(directory: &Path, data: &[u8]) -> Result<String, String> {
    if data.len() < 33
        || data.len() > 20 * 1024 * 1024
        || &data[..8] != b"\x89PNG\r\n\x1a\n"
        || &data[12..16] != b"IHDR"
        || data[8..12] != [0, 0, 0, 13]
    {
        return Err("剪贴板图片须为不超过 20 MB 的 PNG。".into());
    }
    let width = u32::from_be_bytes(data[16..20].try_into().unwrap());
    let height = u32::from_be_bytes(data[20..24].try_into().unwrap());
    if width == 0
        || height == 0
        || width > 16384
        || height > 16384
        || u64::from(width) * u64::from(height) > 40_000_000
    {
        return Err("截图尺寸过大，请保存文件后再拖入。".into());
    }
    fs::create_dir_all(directory).map_err(|error| error.to_string())?;
    let path = directory.join(format!("clipboard-{}.png", uuid::Uuid::new_v4()));
    let mut options = OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
        options.mode(0o600);
        fs::set_permissions(directory, fs::Permissions::from_mode(0o700))
            .map_err(|e| e.to_string())?;
    }
    let mut file = options.open(&path).map_err(|error| error.to_string())?;
    file.write_all(data).map_err(|error| error.to_string())?;
    Ok(path.to_string_lossy().into_owned())
}

#[tauri::command]
pub fn stage_clipboard_image(app: tauri::AppHandle, data: Vec<u8>) -> Result<String, String> {
    let directory = app
        .path()
        .app_config_dir()
        .map_err(|error| error.to_string())?
        .join("attachments");
    save_png(&directory, &data)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn preserves_png_in_unique_private_files_and_rejects_non_images() {
        let directory =
            std::env::temp_dir().join(format!("workbench-clipboard-{}", uuid::Uuid::new_v4()));
        let png = [
            137, 80, 78, 71, 13, 10, 26, 10, 0, 0, 0, 13, 73, 72, 68, 82, 0, 0, 0, 1, 0, 0, 0, 1,
            8, 6, 0, 0, 0, 31, 21, 196, 137, 0, 0, 0, 11, 73, 68, 65, 84, 120, 156, 99, 96, 0, 2,
            0, 0, 5, 0, 1, 165, 246, 69, 64, 0, 0, 0, 0, 73, 69, 78, 68, 174, 66, 96, 130,
        ];
        let first = save_png(&directory, &png).unwrap();
        let second = save_png(&directory, &png).unwrap();
        assert_ne!(first, second);
        assert_eq!(fs::read(&first).unwrap(), png);
        assert!(save_png(&directory, b"not an image").is_err());
        let mut huge = png;
        huge[16..20].copy_from_slice(&20000u32.to_be_bytes());
        assert!(save_png(&directory, &huge).is_err());
        fs::remove_dir_all(directory).unwrap();
    }
}
