"""
HACK TO ENABLE VIRTUAL ENVIRONMENTS UNDER WINDOWS

We have implemented a temporary solution to address the absence of virtual
environment in Windows. The issue stems from the lack of SSL support in our
Python interpreters. As a result, using a virtual environment in Windows
becomes problematic and can lead to the global namespace being cluttered with
unnecessary modules. Worse, this cluttering eventually leads to unexpected
complications.

To circumvent this issue, we have added a directory to the Python path, which
contains an unused SSL module. This workaround enables us to utilize virtual
environments on Windows without encountering any complications.
"""
