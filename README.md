# Steam Integration

GOG Galaxy 2.0 Community integration for Steam.

## Dev env setup
* Download Python 3.7.9 32-bit
* Install it with the defaults
* Create a new virtual env:
    - If you only have python 3.7.9<br/>
    `python.exe -m venv .venv -p "%localappdata%\Programs\Python\Python37-32\python.exe" --pip 22.0.4`
    - IF you have multiple python versions installed (assumes you have `py` as well)<br/>
    `py.exe -3.7 -m venv .venv --pip 22.0.4`
* Activate the virtual env 
  - Windows, Powershell:<br/>
  `.\.venv\Scripts\activate.ps1`
  - MacOS, terminal:<br/>
  `.venv/Scripts/activate`
  
* Install the dev dependencies:
  `pip install -r requirements/dev.txt`
* Make your edits
* Update the protobufs (See README_UPDATE_PROTOBUF_FILES.md for sources)
  Take notice of the initial diff between the files in `protobuf_files` and `protobuf_files/orig`
  Generating the python files is done via:
  `inv GenerateProtobufMessages`
* Build your edits:
  `inv build`
* Test your edits:
  `inv test`
* Install your edits for a local test:
  `inv install`
* Build a release package (zip):
  `inv pack`

This is a fork of the repository from FriendsOfGalaxy, intended to continue development until they resume their work.

**This is unofficial and purely maintained by fans!**

## Installation

*~~The latest release should be available for download via the "Connect" button in Galaxy~~*
We aren't ready to publish this project to Galaxy just yet. We have the tools to do so, but the code is not stable enough for us to consider that just yet. In the meantime, you can either manually patch the existing version, or you can install it a very small subset of the tools the developers use. 

The easiest way to do this is with python installed, following the directions for development, however, you only need to do the first few steps. Once you reach the "Install the dev dependencies," stop there. You don't need all of those packages, you only need invoke. To get it, run<br/>
`pip install invoke==1.2.0`
After that, you can install the custom patch via
`inv install`

If the latest version available on Github is newer than the version specified in the `manifest.json` file in the custom build, Galaxy will download the newer version and replace the files. To prevent this from happening, you can manually set the version in `manifest.json` to a significantly higher value (e.g. `9.9`).

## Why this fork?

Well, without being too complicated, Steam changed how they do authentication. We used to be able to use one call and magically get a lot of info we needed. But, if we're being honest, it was a little insecure, and it easy. While we were using it for legitimate purposes, not everyone else was, and one of Valve's greatest deterrence to botting or DOS attacks (etc) is making things difficult. The new workflow uses a significant back-and-forth between a Steam Server and ourselves, closely resembling a common web form of authentication called OAuth2. This meant a lot of under-the-hood changes. 

## Credits

### Current Version:
This is a fork of https://github.com/FriendsOfGalaxy/galaxy-integration-steam

The new Authorization flow implementation is heavily influenced by SteamKit. https://github.com/SteamRE/SteamKit<br/>
While we have not utilized their source code, they have implemented the new authentication workflow before we did, and we used their knowledge of how to do so in order to implement it ourselves. If you are doing anything steam related in C#, you should check them out; their project has far more features than our own.

Some work was influenced by ValvePython. https://github.com/ValvePython/steam<br/>
Our projects do the same thing, but use different methods (we use asyncio, they use gevent, for example). Both projects were working on the new Auth Flow simultaneously, with little collaboration between us. That said, their scope is much larger than our own and lets you do a lot more things. If you are looking for a python means of implementing a steam network authentication, you should use their work instead.

### Original Version:

Original Plugin was based on work and research done by others:
* https://github.com/prncc/steam-scraper
* https://github.com/rhaarm/steam-scraper
* https://github.com/mulhod/steam_reviews
* https://github.com/summersb92/aeolipile
* https://github.com/rcpoison/steam-scraper
* https://github.com/chmccc/steam-scraper
