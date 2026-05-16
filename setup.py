from setuptools import setup, find_packages

setup(
    name="rs-image-api",
    version="0.1.0",
    description="Multi-source Remote Sensing Imagery Downloader (Google/Bing/Esri)",
    author="24kchengYe",
    packages=find_packages(),
    install_requires=[
        "requests>=2.28.0",
        "Pillow>=9.0.0",
        "geopandas>=0.12.0",
        "numpy>=1.21.0",
    ],
    entry_points={
        "console_scripts": [
            "rs-image=rs_image_api.cli:main",
        ],
    },
    python_requires=">=3.9",
)
