#!/usr/bin/env python3
# Required Notice: Copyright Matthew Armstrong (https://github.com/The-Dorkknight)
# Licensed under the PolyForm Noncommercial License 1.0.0. No warranty.
"""
pip packaging for wyse_injector.py. Not required to use the tool - run it
directly with `python3 wyse_injector.py`, or via the WyseInjector-Linux.sh
launcher - this is here for anyone who'd rather `pip install .` it and get
a `wyse-injector` command on their PATH.
"""
from setuptools import setup

with open("README.md", encoding="utf-8") as f:
    long_description = f.read()

setup(
    name="wyse-injector",
    version="0.2.0.dev0",  # PEP 440 doesn't allow "0.2.0-wip"; this stays in sync with
                            # APP_VERSION in wyse_injector.py by hand, by design (no build step)
    description="Builds an unattended Debian installer ISO that turns a Dell Wyse 3040 "
                "thin client into a headless Klipper/Moonraker 3D-printer controller.",
    long_description=long_description,
    long_description_content_type="text/markdown",
    author="Matthew Armstrong",
    author_email="The-Dorkknight@users.noreply.github.com",
    url="https://github.com/The-Dorkknight/klipper-wyse3040-installer",
    license="PolyForm-Noncommercial-1.0.0",
    py_modules=["wyse_injector"],
    python_requires=">=3.8",
    install_requires=[],  # standard library only - see requirements.txt
    entry_points={"console_scripts": ["wyse-injector=wyse_injector:main"]},
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Environment :: X11 Applications",
        "Intended Audience :: Developers",
        "Intended Audience :: Manufacturing",
        "License :: Other/Proprietary License",
        "Operating System :: POSIX :: Linux",
        "Programming Language :: Python :: 3",
        "Topic :: System :: Installation/Setup",
    ],
)
