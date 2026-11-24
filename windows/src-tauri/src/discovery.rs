use mdns_sd::{ServiceDaemon, ServiceInfo};
use sha2::{Digest, Sha256};
use std::{fs, path::PathBuf, sync::Mutex};

const SERVICE_TYPE: &str = "_agent-wb._tcp.local.";

pub fn fingerprint(key: &str) -> Option<String> {
    use base64::{Engine, engine::general_purpose::STANDARD};
    let fields: Vec<_> = key.split_whitespace().collect();
    if fields.len() < 2 || fields[0] != "ssh-ed25519" || STANDARD.decode(fields[1]).ok()?.len() != 51 {
        return None;
    }
    Some(format!("{:x}", Sha256::digest(fields[..2].join(" ").as_bytes())))
}

#[derive(Default)]
pub struct Publisher {
    current: Mutex<Option<(ServiceDaemon, String, u16)>>,
}

impl Publisher {
    pub fn set(&self, enabled: bool, port: u16) -> Result<(), String> {
        let mut current = self.current.lock().map_err(|_| "自动发现状态不可用")?;
        if enabled && current.as_ref().is_some_and(|(_, _, old_port)| *old_port == port) {
            return Ok(());
        }
        if let Some((daemon, name, _)) = current.take() {
            let _ = daemon.unregister(&name);
            let _ = daemon.shutdown();
        }
        if !enabled { return Ok(()); }
        let key_path = PathBuf::from(std::env::var_os("ProgramData").ok_or("请先安装 Windows OpenSSH")?)
            .join("ssh/ssh_host_ed25519_key.pub");
        let key = fs::read_to_string(key_path).map_err(|_| "请先启动 OpenSSH，再启用自动发现")?;
        let key = fingerprint(&key).ok_or("电脑主机密钥格式无效")?;
        let short = &key[..20];
        let properties = [("v", "1"), ("key", key.as_str()), ("platform", "windows")];
        let info = ServiceInfo::new(SERVICE_TYPE, &format!("wb-{short}"), &format!("wb-{short}.local."),
            "", port, &properties[..]).map_err(|e| e.to_string())?.enable_addr_auto();
        let name = info.get_fullname().to_owned();
        let daemon = ServiceDaemon::new().map_err(|e| e.to_string())?;
        if let Err(error) = daemon.register(info) { let _ = daemon.shutdown(); return Err(error.to_string()); }
        *current = Some((daemon, name, port));
        Ok(())
    }
}

impl Drop for Publisher {
    fn drop(&mut self) { let _ = self.set(false, 22); }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn discovery_identity_uses_the_pinned_ssh_key() {
        let key = "ssh-ed25519 EQUXQ2QhaI9pYZA3CID2ZYUNKAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA";
        assert_eq!(fingerprint(key), fingerprint(&format!("{key} ignored-comment")));
        assert_ne!(fingerprint(key), fingerprint(&key.replace("AAAA", "BBBB")));
        assert!(fingerprint("ssh-ed25519 invalid").is_none());
    }
}
