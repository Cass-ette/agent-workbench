use portable_pty::Child;
use std::{
    io, mem,
    os::windows::io::{AsRawHandle, FromRawHandle, OwnedHandle},
    ptr, thread,
    time::{Duration, Instant},
};
use windows_sys::Win32::System::{
    JobObjects::{
        AssignProcessToJobObject, CreateJobObjectW, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
        JOBOBJECT_BASIC_ACCOUNTING_INFORMATION, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
        JobObjectBasicAccountingInformation, JobObjectExtendedLimitInformation,
        QueryInformationJobObject, SetInformationJobObject, TerminateJobObject,
    },
    Threading::{CreateEventW, SetEvent},
};

pub(super) struct TerminalJob {
    job: OwnedHandle,
    ready: OwnedHandle,
    event_name: String,
}

impl TerminalJob {
    pub(super) fn new() -> io::Result<Self> {
        let job = unsafe { CreateJobObjectW(ptr::null(), ptr::null()) };
        if job.is_null() {
            return Err(io::Error::last_os_error());
        }
        let job = unsafe { OwnedHandle::from_raw_handle(job) };

        let mut limits = JOBOBJECT_EXTENDED_LIMIT_INFORMATION::default();
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        if unsafe {
            SetInformationJobObject(
                job.as_raw_handle(),
                JobObjectExtendedLimitInformation,
                ptr::from_ref(&limits).cast(),
                mem::size_of_val(&limits) as u32,
            )
        } == 0
        {
            return Err(io::Error::last_os_error());
        }

        let event_name = format!("Local\\AgentWorkbench-{}", uuid::Uuid::new_v4());
        let wide_name: Vec<u16> = event_name.encode_utf16().chain([0]).collect();
        let ready = unsafe { CreateEventW(ptr::null(), 1, 0, wide_name.as_ptr()) };
        if ready.is_null() {
            return Err(io::Error::last_os_error());
        }
        let ready = unsafe { OwnedHandle::from_raw_handle(ready) };
        Ok(Self {
            job,
            ready,
            event_name,
        })
    }

    pub(super) fn startup_script(&self) -> String {
        // ConPTY starts the shell immediately. Hold it at the gate before loading
        // profiles or invoking anything that can create a child process.
        format!(
            "try {{ \
                $workbenchReady = [Threading.EventWaitHandle]::OpenExisting('{}'); \
                if (!$workbenchReady.WaitOne(10000)) {{ exit 1 }} \
            }} catch {{ [Console]::Error.WriteLine('Workbench terminal startup failed.'); exit 1 }} \
            finally {{ if ($workbenchReady) {{ $workbenchReady.Dispose() }} }}; \
            Remove-Variable workbenchReady -ErrorAction SilentlyContinue; ",
            self.event_name,
        )
    }

    pub(super) fn attach(&self, child: &dyn Child) -> io::Result<()> {
        let process = child
            .as_raw_handle()
            .ok_or_else(|| io::Error::other("missing process handle"))?;
        if unsafe { AssignProcessToJobObject(self.job.as_raw_handle(), process) } == 0 {
            return Err(io::Error::last_os_error());
        }
        if unsafe { SetEvent(self.ready.as_raw_handle()) } == 0 {
            return Err(io::Error::last_os_error());
        }
        Ok(())
    }

    pub(super) fn terminate_and_wait(&self) -> io::Result<()> {
        if self.active_processes()? == 0 {
            return Ok(());
        }
        if unsafe { TerminateJobObject(self.job.as_raw_handle(), 1) } == 0 {
            return Err(io::Error::last_os_error());
        }
        let deadline = Instant::now() + Duration::from_secs(5);
        while self.active_processes()? != 0 {
            if Instant::now() >= deadline {
                return Err(io::Error::new(
                    io::ErrorKind::TimedOut,
                    "terminal processes are still running",
                ));
            }
            thread::sleep(Duration::from_millis(20));
        }
        Ok(())
    }

    fn active_processes(&self) -> io::Result<u32> {
        let mut accounting = JOBOBJECT_BASIC_ACCOUNTING_INFORMATION::default();
        if unsafe {
            QueryInformationJobObject(
                self.job.as_raw_handle(),
                JobObjectBasicAccountingInformation,
                ptr::from_mut(&mut accounting).cast(),
                mem::size_of_val(&accounting) as u32,
                ptr::null_mut(),
            )
        } == 0
        {
            return Err(io::Error::last_os_error());
        }
        Ok(accounting.ActiveProcesses)
    }
}
