#!/usr/bin/env python3
"""Regenerate eshu-gateway-install.sh from the template + source files.
The authoritative gateway and poller scripts live in separate files so they
can be syntax-checked independently (bash -n *.sh). The installer is a
self-contained concatenation for the gateway update pipeline.

Usage:
    python3 gen_installer.py
    git diff           # verify changes
    git add -A && git commit
"""
import sys
import os

script_dir = os.path.dirname(os.path.abspath(__file__))

def read(path):
    with open(os.path.join(script_dir, path), "r", encoding="utf-8") as f:
        return f.read()

def write(path, content):
    with open(os.path.join(script_dir, path), "w", encoding="utf-8") as f:
        f.write(content)

def main():
    template  = read("eshu-installer-template.sh")
    gateway   = read("eshu-gateway.sh")
    poller    = read("eshu-poller.sh")
    logger    = read("eshu-logger.sh")

    if "__GATEWAY_CONTENT__" not in template:
        sys.exit("ERROR: __GATEWAY_CONTENT__ not found in template")
    if "__POLLER_CONTENT__" not in template:
        sys.exit("ERROR: __POLLER_CONTENT__ not found in template")
    if "__LOGGER_CONTENT__" not in template:
        sys.exit("ERROR: __LOGGER_CONTENT__ not found in template")

    installer = template.replace("__GATEWAY_CONTENT__", gateway.rstrip("\n"))
    installer = installer.replace("__POLLER_CONTENT__", poller.rstrip("\n"))
    installer = installer.replace("__LOGGER_CONTENT__", logger.rstrip("\n"))

    write("eshu-gateway-install.sh", installer)
    write("static/eshu-gateway-install.sh", installer)

    print("Generated eshu-gateway-install.sh (source + static/)")
    print("Run `push to dev gateways` in the UI to deploy Edge to dev gateways.")
    print("Verify with: bash -n dashboard/eshu-gateway-install.sh")

if __name__ == "__main__":
    main()
