#ifndef AppVersion
  #define AppVersion "0.11.62"
#endif

#define ProjectRoot SourcePath + "\..\.."
#define ReaderDist ProjectRoot + "\dist\MangaX-Reader"
#define InstallerOutput ProjectRoot + "\dist\installers"
#define ReaderExe "MangaX-Reader.exe"

[Setup]
AppId={{B76E8125-94CA-4A87-BBB5-5B4B74367F31}
AppName=MangaX Reader
AppVersion={#AppVersion}
AppVerName=MangaX Reader v{#AppVersion}
AppPublisher=MangaX
DefaultDirName={localappdata}\Programs\MangaX Reader
DefaultGroupName=MangaX Reader
DisableDirPage=no
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir={#InstallerOutput}
OutputBaseFilename=MangaX-Reader-Setup-v{#AppVersion}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#ReaderExe}
UninstallDisplayName=MangaX Reader
SetupLogging=yes

[Languages]
Name: "turkish"; MessagesFile: "compiler:Languages\Turkish.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#ReaderDist}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\MangaX Reader"; Filename: "{app}\{#ReaderExe}"; WorkingDir: "{app}"
Name: "{autodesktop}\MangaX Reader"; Filename: "{app}\{#ReaderExe}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#ReaderExe}"; Description: "{cm:LaunchProgram,MangaX Reader}"; WorkingDir: "{app}"; Flags: nowait postinstall skipifsilent
