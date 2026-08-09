#define MyAppName "PhoEniX BPU"
#define MyAppVersion GetEnv("PHOENIX_VERSION")
#if MyAppVersion == ""
#define MyAppVersion "1.4.0"
#endif
#define MyAppPublisher "SAPTA"
#define MyAppExeName "run.cmd"

[Setup]
AppId={{88E7A8A1-A5B2-45EF-9F4D-7E8B49D0305A}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\PhoEniX BPU
DefaultGroupName=PhoEniX BPU
DisableProgramGroupPage=yes
OutputDir=..\dist\installer
OutputBaseFilename=PhoEniX_BPU_Setup_{#MyAppVersion}
Compression=lzma
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
UninstallDisplayIcon={app}\run.cmd

[Languages]
Name: "french"; MessagesFile: "compiler:Languages\French.isl"

[Tasks]
Name: "desktopicon"; Description: "Creer un raccourci sur le Bureau"; GroupDescription: "Raccourcis:"; Flags: unchecked

[Files]
Source: "..\app.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\db.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\requirements.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\package.json"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\package-lock.json"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\RELEASE_CHECKLIST.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\setup.cmd"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\run.cmd"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\controle-visuel.cmd"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\visual.config.json"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\docs\*"; DestDir: "{app}\docs"; Flags: ignoreversion recursesubdirs
Source: "..\database\*"; DestDir: "{app}\database"; Flags: ignoreversion recursesubdirs; Excludes: "__pycache__\*,*.pyc,*.pyo,*.log"
Source: "..\scripts\*"; DestDir: "{app}\scripts"; Flags: ignoreversion recursesubdirs; Excludes: "__pycache__\*,*.pyc,*.pyo,*.log"
Source: "..\services\*"; DestDir: "{app}\services"; Flags: ignoreversion recursesubdirs; Excludes: "__pycache__\*,*.pyc,*.pyo,*.log"
Source: "..\static\*"; DestDir: "{app}\static"; Flags: ignoreversion recursesubdirs
Source: "..\templates\*"; DestDir: "{app}\templates"; Flags: ignoreversion recursesubdirs
Source: "..\tests\*"; DestDir: "{app}\tests"; Flags: ignoreversion recursesubdirs; Excludes: "__pycache__\*,*.pyc,*.pyo,*.log"

[Dirs]
Name: "{app}\data"
Name: "{app}\uploads"
Name: "{app}\exports"
Name: "{app}\backups"
Name: "{app}\output"

[Icons]
Name: "{group}\PhoEniX BPU"; Filename: "{app}\run.cmd"; WorkingDir: "{app}"
Name: "{group}\Configuration PhoEniX BPU"; Filename: "{app}\setup.cmd"; WorkingDir: "{app}"
Name: "{group}\Desinstaller PhoEniX BPU"; Filename: "{uninstallexe}"
Name: "{autodesktop}\PhoEniX BPU"; Filename: "{app}\run.cmd"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\setup.cmd"; WorkingDir: "{app}"; Description: "Installer les dependances et initialiser PhoEniX BPU"; Flags: postinstall skipifsilent
