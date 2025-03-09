from setuptools import find_packages, setup

# Read the README file for the long description
with open("README.md", "r", encoding="utf-8") as f:
    long_description = f.read()

setup(
    name="PlexSyncer",
    version="0.1.0",
    packages=find_packages(),
    install_requires=[
        "plexapi",
        "requests",
        "mutagen",
    ],
    entry_points={
        "console_scripts": [
            "plexsyncer=cli:main",
        ],
    },
    # Additional metadata
    author="KnightRider2070",
    description="A tool to generate and upload Plex playlists.",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/KnightRider2070/PlexSyncer",
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: Mozilla Public License 2.0 (MPL 2.0)",
        "Operating System :: OS Independent",
        "Intended Audience :: Developers",
        "Topic :: Multimedia :: Sound/Audio",
    ],
    python_requires=">=3.6",
    include_package_data=True,
)
