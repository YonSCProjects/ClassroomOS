"""
ClassroomOS — Windows Service Wrapper
========================================
Wraps agent_main.py as a proper Windows service using pywin32.
The service:
  - Auto-starts with Windows
  - Runs as SYSTEM (needed for USB/firewall/registry control)
  - Restarts automatically on crash (configured in install_agent.ps1)
  - Students cannot stop it from Task Manager (service is protected)

Install / uninstall:
    python agent_service.py install
    python agent_service.py start
    python agent_service.py stop
    python agent_service.py remove
    
Or just run install_agent.ps1 which does all of this automatically.
"""

import sys
import os
import logging
import threading

# pywin32 imports
import win32serviceutil
import win32service
import win32event
import servicemanager

# Pull in agent main
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, os.path.join(BASE_DIR, "..", "shared"))

from agent_main import main as agent_main

SERVICE_NAME    = "ClassroomOSAgent"
SERVICE_DISPLAY = "ClassroomOS Agent"
SERVICE_DESC    = "ClassroomOS client management agent. Do not stop this service."


class ClassroomOSService(win32serviceutil.ServiceFramework):
    _svc_name_         = SERVICE_NAME
    _svc_display_name_ = SERVICE_DISPLAY
    _svc_description_  = SERVICE_DESC

    def __init__(self, args):
        win32serviceutil.ServiceFramework.__init__(self, args)
        self._stop_event = win32event.CreateEvent(None, 0, 0, None)
        self._thread     = None

        # Set up logging to Windows Event Log + file
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            handlers=[
                logging.FileHandler(
                    os.path.join(BASE_DIR, "agent.log"),
                    encoding="utf-8"
                ),
            ],
        )

    def SvcStop(self):
        """Called by Windows when the service is stopped."""
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        win32event.SetEvent(self._stop_event)

    def SvcDoRun(self):
        """Main service entry point."""
        servicemanager.LogMsg(
            servicemanager.EVENTLOG_INFORMATION_TYPE,
            servicemanager.PYS_SERVICE_STARTED,
            (self._svc_name_, ""),
        )

        # Run the agent on a daemon thread so we can respond to stop signals
        self._thread = threading.Thread(target=agent_main, daemon=True, name="AgentMain")
        self._thread.start()

        # Block until stop is signalled
        win32event.WaitForSingleObject(self._stop_event, win32event.INFINITE)

        servicemanager.LogMsg(
            servicemanager.EVENTLOG_INFORMATION_TYPE,
            servicemanager.PYS_SERVICE_STOPPED,
            (self._svc_name_, ""),
        )


if __name__ == "__main__":
    if len(sys.argv) == 1:
        # Run from SCM
        servicemanager.Initialize()
        servicemanager.PrepareToHostSingle(ClassroomOSService)
        servicemanager.StartServiceCtrlDispatcher()
    else:
        win32serviceutil.HandleCommandLine(ClassroomOSService)
