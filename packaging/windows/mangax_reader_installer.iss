#ifndef AppVersion
  #define AppVersion "0.12.3"
#endif

#define ProjectRoot SourcePath + "\..\.."
#define ReaderDist ProjectRoot + "\dist\MangaX-Reader"
#define InstallerOutput ProjectRoot + "\dist\installers"
#define ReaderExe "MangaX-Reader.exe"

[Setup]
#ifdef TestBuild
AppId={{C215B209-1D88-47B0-AD47-0A955A81F8DE}
#else
AppId={{B76E8125-94CA-4A87-BBB5-5B4B74367F31}
#endif
AppName=MangaX Reader
AppVersion={#AppVersion}
AppVerName=MangaX Reader v{#AppVersion}
AppPublisher=MangaX
DefaultDirName={localappdata}\Programs\MangaX
DefaultGroupName=MangaX
DisableDirPage=no
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
UsePreviousAppDir=yes
UsePreviousGroup=no
UsePreviousTasks=yes
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
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "turkish"; MessagesFile: "compiler:Languages\Turkish.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#ReaderDist}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
Type: filesandordirs; Name: "{app}\_internal"
Type: filesandordirs; Name: "{app}\static"
Type: files; Name: "{autodesktop}\MangaX Reader.lnk"
Type: filesandordirs; Name: "{autoprograms}\MangaX Reader"

[Icons]
Name: "{group}\MangaX"; Filename: "{app}\{#ReaderExe}"; WorkingDir: "{app}"
Name: "{autodesktop}\MangaX"; Filename: "{app}\{#ReaderExe}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#ReaderExe}"; Description: "{cm:LaunchProgram,MangaX Reader}"; WorkingDir: "{app}"; Flags: nowait postinstall skipifsilent
