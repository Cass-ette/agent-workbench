use portable_pty::Child;
use std::{
    io, mem,
    os::windows::io::{AsRawHandle, FromRawHandle, OwnedHandle},
    ptr, thread,
    time::{Duration, Instant},
};
use windows_sys::Win32::{
    Foundation::{ERROR_INVALID_PARAMETER, ERROR_MORE_DATA, WAIT_OBJECT_0, WAIT_TIMEOUT},
    System::{
        JobObjects::{
            AssignProcessToJobObject, CreateJobObjectW, IsProcessInJob,
            JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE, JOBOBJECT_BASIC_ACCOUNTING_INFORMATION,
            JOBOBJECT_BASIC_PROCESS_ID_LIST, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
            JobObjectBasicAccountingInformation, JobObjectBasicProcessIdList,
            JobObjectExtendedLimitInformation, QueryInformationJobObject, SetInformationJobObject,
            TerminateJobObject,
        },
        Threading::{
            CreateEventW, OpenProcess, PROCESS_QUERY_LIMITED_INFORMATION, PROCESS_SYNCHRONIZE,
            SetEvent, WaitForSingleObject,
        },
    },
};

pub(super) struct TerminalJob {
    job: OwnedHandle,
    ready: OwnedHandle,
    event_name: String,
    stopping: Vec<OwnedHandle>,
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
            stopping: Vec::new(),
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

    pub(super) fn terminate_and_wait(&mut self) -> io::Result<()> {
        let active = self.active_processes()?;
        if active != 0 {
            // The job's process count can reach zero before the processes release
            // their open files. Keep handles so we can wait for each process to exit.
            let processes = self.processes(active)?;
            self.stopping.extend(processes);
            if unsafe { TerminateJobObject(self.job.as_raw_handle(), 1) } == 0 {
                return Err(io::Error::last_os_error());
            }
        }
        let deadline = Instant::now() + Duration::from_secs(5);
        let timeout = || {
            io::Error::new(
                io::ErrorKind::TimedOut,
                "terminal processes are still running",
            )
        };
        for process in &self.stopping {
            let millis = deadline
                .saturating_duration_since(Instant::now())
                .as_millis() as u32;
            match unsafe { WaitForSingleObject(process.as_raw_handle(), millis) } {
                WAIT_OBJECT_0 => {}
                WAIT_TIMEOUT => return Err(timeout()),
                _ => return Err(io::Error::last_os_error()),
            }
        }
        while self.active_processes()? != 0 {
            if Instant::now() >= deadline {
                return Err(timeout());
            }
            thread::sleep(Duration::from_millis(20));
        }
        self.stopping.clear();
        Ok(())
    }

    fn processes(&self, estimate: u32) -> io::Result<Vec<OwnedHandle>> {
        const HEADER: usize = mem::offset_of!(JOBOBJECT_BASIC_PROCESS_ID_LIST, ProcessIdList)
            / mem::size_of::<usize>();
        let mut capacity = estimate.max(8) as usize;
        let (buffer, count) = loop {
            // The Win32 structure ends in a variable-length array of pointer-sized IDs.
            let mut buffer = vec![0usize; HEADER + capacity];
            if unsafe {
                QueryInformationJobObject(
                    self.job.as_raw_handle(),
                    JobObjectBasicProcessIdList,
                    buffer.as_mut_ptr().cast(),
                    mem::size_of_val(buffer.as_slice()) as u32,
                    ptr::null_mut(),
                )
            } != 0
            {
                let header = unsafe { &*buffer.as_ptr().cast::<JOBOBJECT_BASIC_PROCESS_ID_LIST>() };
                break (buffer, header.NumberOfProcessIdsInList as usize);
            }
            let error = io::Error::last_os_error();
            if error.raw_os_error() != Some(ERROR_MORE_DATA as i32) {
                return Err(error);
            }
            capacity *= 2;
        };

        let mut processes = Vec::with_capacity(count);
        for &id in &buffer[HEADER..HEADER + count] {
            let process = unsafe {
                OpenProcess(
                    PROCESS_SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION,
                    0,
                    id as u32,
                )
            };
            if process.is_null() {
                let error = io::Error::last_os_error();
                if error.raw_os_error() == Some(ERROR_INVALID_PARAMETER as i32) {
                    continue; // The process exited before we could open it.
                }
                return Err(error);
            }
            let process = unsafe { OwnedHandle::from_raw_handle(process) };
            let mut in_job = 0;
            if unsafe {
                IsProcessInJob(
                    process.as_raw_handle(),
                    self.job.as_raw_handle(),
                    &mut in_job,
                )
            } == 0
            {
                return Err(io::Error::last_os_error());
            }
            if in_job != 0 {
                processes.push(process);
            }
        }
        Ok(processes)
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
