from setuptools import find_packages, setup

setup(
    name="octotail",
    version="0.0.1",
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
)
