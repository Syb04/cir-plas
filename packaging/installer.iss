; Inno Setup script for CirPlas (built by CI after PyInstaller: dist/CirPlas/)
#define MyAppName "CirPlas"
#ifndef MyAppVersion
  #define MyAppVersion "0.1.0"
#endif
#define MyAppExeName "cirplas-gui.exe"

[Setup]
AppId={{7D5C2A6E-3B1F-4C8A-9E2D-CIRPLAS00001}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=CirPlas project
DefaultDirName={autopf}\CirPlas
DefaultGroupName=CirPlas
OutputDir=..\dist
OutputBaseFilename=CirPlas-{#MyAppVersion}-setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
ChangesAssociations=yes
LicenseFile=..\LICENSE

[Languages]
Name: "japanese"; MessagesFile: "compiler:Languages\Japanese.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\dist\CirPlas\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\THIRD_PARTY_NOTICES.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\docs\spec\*"; DestDir: "{app}\docs\spec"; Flags: ignoreversion recursesubdirs

[Icons]
Name: "{group}\CirPlas"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\CirPlas 仕様書"; Filename: "{app}\docs\spec\README.md"
Name: "{autodesktop}\CirPlas"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Registry]
Root: HKA; Subkey: "Software\Classes\.cirplas.json"; ValueType: string; ValueName: ""; ValueData: "CirPlasProject"; Flags: uninsdeletevalue
Root: HKA; Subkey: "Software\Classes\CirPlasProject"; ValueType: string; ValueName: ""; ValueData: "CirPlas project"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\CirPlasProject\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExeName}"" ""%1"""

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,CirPlas}"; Flags: nowait postinstall skipifsilent
