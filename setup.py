from codecs import open
from os import path

from setuptools import find_packages, setup

dot = path.abspath(path.dirname(__file__))

# get the dependencies and installs
with open(path.join(dot, "requirements.txt"), encoding="utf-8") as f:
    all_reqs = f.read().split("\n")

install_requires = [x.strip() for x in all_reqs if "git+" not in x]
dependency_links = [x.strip().replace("git+", "") for x in all_reqs if x.startswith("git+")]

setup(
    name="octotail",
    version="1.0.0",
    description="Live tail GitHub Actions runs on git push.",
    entry_points={
        "console_scripts": ["octotail=octotail.main:_main"],
    },
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Developers",
        "Programming Language :: Python :: 3",
    ],
    license="UNLICENSE",
    keywords="github-actions, tail, post-receive, codecrafters, git",
    packages=find_packages(exclude=["docs", "tests*"]),
    include_package_data=True,
    install_requires=install_requires,
    dependency_links=dependency_links,
)
