#define MyAppName "PhoEniX BPU"
#define MyAppVersion GetEnv("PHOENIX_VERSION")
#if MyAppVersion == ""
#define MyAppVersion "2.5.2"
#endif
#define MyAppPublisher "SAPTA"
#define MyAppExeName "PhoEniX BPU.exe"
#define MyAppId GetEnv("PHOENIX_APP_ID")
#if MyAppId == ""
#define MyAppId "{{88E7A8A1-A5B2-45EF-9F4D-7E8B49D0305A}"
#endif
#define MyOutputSuffix GetEnv("PHOENIX_OUTPUT_SUFFIX")

[Setup]
AppId={#MyAppId}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\PhoEniX BPU
DefaultGroupName=PhoEniX BPU
DisableProgramGroupPage=yes
OutputDir=..\dist\installer
OutputBaseFilename=PhoEniX_BPU_Setup_{#MyAppVersion}{#MyOutputSuffix}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
CloseApplications=yes
RestartApplications=no
SetupLogging=yes
UninstallDisplayIcon={app}\{#MyAppExeName}
VersionInfoVersion={#MyAppVersion}.0
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription=Installation de {#MyAppName}
VersionInfoProductName={#MyAppName}
VersionInfoProductVersion={#MyAppVersion}

[Languages]
Name: "french"; MessagesFile: "compiler:Languages\French.isl"

[Tasks]
Name: "desktopicon"; Description: "Créer un raccourci sur le Bureau"; GroupDescription: "Raccourcis :"; Flags: unchecked

[Files]
Source: "..\dist\desktop\PhoEniX BPU\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\PhoEniX BPU"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"
Name: "{group}\Désinstaller PhoEniX BPU"; Filename: "{uninstallexe}"
Name: "{autodesktop}\PhoEniX BPU"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Description: "Lancer PhoEniX BPU"; Flags: postinstall nowait skipifsilent unchecked
