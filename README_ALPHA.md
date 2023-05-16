# Pre-Alpha Testing

If you're here, you know things could be (and likely will be) broken. You are here because we are few, but you are many. It's easier to find bugs and such by making you do it. 

## Setup (Windows)
* Download Python 3.7.9, 32-bit (64-bit also works ~~, as does any newer version. Be warned, if you are using a new version and write any code, it may have unexpected results if you apply it to your plugin. You should leave that to us, so this wont be an issue)~~ Because Python loves to introduce breaking changes, you must use this version. If you have multiple versions of pythin installed, make sure you are using this one. 
* Install it with the defaults
* Set up your working directory. this will be the place you download or clone this repo to. 
* Go to working directory and open powershell. 
* Info above commands will remove .venv folder from directory. It is expected that it will be a folder with virtual python env.
If you want only to install and test it run
* Run .\CompileAndInstallForWindows.ps1
If you want to develop run:
* ./PrepareDevelopmentEnv.ps1

**Warning commands below will leave you in python virtual envioriment if you only wanted to install just close windows after installation is done. If you are developer well I hope that you know what is python virtual env. You can tell that any of above command completed because you will see (.venv) PS at the start of command line when they will complete **

## Setup (MacOS)
* Download Python 3.7.9, 32-bit (64-bit also works, as does any newer version. Be warned, if you are using a new version and write any code, it may have unexpected results if you apply it to your plugin. You should leave that to us, so this wont be an issue)
* Install it with the defaults
* Set up your working directory. this will be the place you download or clone this repo to. 
* Create a new virtual env:
  `python -m virtualenv .venv --pip 22.0.4`
* Activate the virtual env:
  `./.venv/Scripts/activate`
* Install the dev dependencies (optional). This will let you potentially help us debug, but isn't really necessary if you don't feel comfortable monkeying around the code. 
  `pip install -r requirements/dev.txt`
* Backup the current installation of steam (optional) we will overwrite this in the next command.
* Install the plugin in it's buggy glory:
  `inv install`

 ## The Logs

 We need them. We will ask for them when something breaks. For windows, they are located at `C:\ProgramData\GOG.com\Galaxy\logs`. For MacOS, they are located at `/Users/Shared/GOG.com/Galaxy/Logs`
 When you first install the plugin, make sure to delete `plugin-steam-<whatever gibberish is here>.log`. It makes it easier to start with a fresh log than read through old stuff. 
