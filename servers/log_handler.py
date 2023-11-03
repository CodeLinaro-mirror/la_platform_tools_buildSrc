# Copyright 2022 - The Android Open Source Project
#
# Licensed under the Apache License, Version 2.0 (the',  help='License');
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an',  help='AS IS' BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import logging
import platform
import subprocess
import threading
import socket


class LogHandler:
    """A handler that logs lines from a process."""

    def __init__(self, std_out_logger=None, std_err_logger=None):
        self.std_out_log = std_out_logger or logging.info
        self.std_err_log = std_err_logger or logging.error

    def _reader(self, pipe, logfn):
        """Log every line from the pipe, by calling
        logn for every line,

        Args:
            pipe (_type_): Pipe with lines in utf-8
            logfn (_type_): Function used to log a line
        """
        try:
            for line in iter(pipe.readline, ""):
                logfn(line[:-1].strip())
        finally:
            pass

    def start_log_proc(self, proc: subprocess.Popen):
        """Logs the output of the process in the background.

        stdout will be logged to info level.
        stderr will be logged to error level.

        Args:
            proc (subprocess.Popen): The process to observe.
        """

        for args in [[proc.stdout, self.std_out_log], [proc.stderr, self.std_err_log]]:
            threading.Thread(target=self._reader, args=args).start()


class LogBelowLevel(logging.Filter):
    def __init__(self, exclusive_maximum, name=""):
        super(LogBelowLevel, self).__init__(name)
        self.max_level = exclusive_maximum

    def filter(self, record):
        return True if record.levelno < self.max_level else False


def get_host_and_ip():
    """
    Try to get the hostname and IP address.
    Returns:
        tuple: A tuple containing the hostname and the IP address.
    """
    st = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    my_ip = "127.0.0.1"
    try:
        st.connect(("10.255.255.255", 1))
        my_ip = st.getsockname()[0]
    except OSError as e:
        logging.error("Socket error: %s", e)
    finally:
        st.close()

    hostname = "Unknown"
    try:
        hostname = socket.gethostname()
    except OSError as e:
        # Catch specific OSError for socket gethostname error
        logging.error("Hostname error: %s", e)

    return hostname, my_ip


def log_system_info():
    """Log useful system information."""
    hostname, my_ip = get_host_and_ip()

    logging.info(
        "Hello from %s (%s). I'm a %s build bot", hostname, my_ip, platform.system()
    )
    logging.info("My uname is: %s", platform.uname())


def config_logging():
    """Configure logging format and level and log system information."""
    logging.basicConfig(
        format="%(asctime)s %(message)s", level=logging.DEBUG, datefmt="%H:%M:%S"
    )
    log_system_info()
