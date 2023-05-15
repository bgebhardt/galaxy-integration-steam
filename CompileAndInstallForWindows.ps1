if (Test-Path -Path .\.venv) {
rm .\.venv -r -force
}
py.exe -3.7 -m venv .venv
.\.venv\Scripts\activate.ps1
# Install newer version of pip.
python.exe -m pip install --upgrade pip==22.0.4
# Install wheel - some dependencies will warn us that wheel is unaccessible and they must install using old install sript
pip install wheel
pip install -r requirements/app.txt
inv install