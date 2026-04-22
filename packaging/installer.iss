; Inno Setup script for Cove GIF Maker (Windows)
; Invoked from build.ps1 via:
;   iscc /DAppVersion=X.Y.Z /DSourceDir=<abs dist\cove-gif-maker> \
;        /DOutputDir=<abs release> /DIconFile=<abs cove_icon.ico> installer.iss

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\dist\cove-gif-maker"
#endif
#ifndef OutputDir
  #define OutputDir "..\release"
#endif
#ifndef IconFile
  #define IconFile "..\cove_icon.ico"
#endif

[Setup]
AppId={{9C7B9D82-0E88-4E1F-A3E4-9E1F0C8B7C10}
AppName=Cove GIF Maker
AppVersion={#AppVersion}
AppPublisher=Cove
AppPublisherURL=https://github.com/Sin213/cove-gif-maker
AppSupportURL=https://github.com/Sin213/cove-gif-maker/issues
AppUpdatesURL=https://github.com/Sin213/cove-gif-maker/releases
DefaultDirName={autopf}\Cove GIF Maker
DefaultGroupName=Cove GIF Maker
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\cove-gif-maker.exe
Compression=lzma2/max
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible
ArchitecturesAllowed=x64compatible
OutputDir={#OutputDir}
OutputBaseFilename=cove-gif-maker-{#AppVersion}-Setup
SetupIconFile={#IconFile}
WizardStyle=modern
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Cove GIF Maker"; Filename: "{app}\cove-gif-maker.exe"
Name: "{group}\Uninstall Cove GIF Maker"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Cove GIF Maker"; Filename: "{app}\cove-gif-maker.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\cove-gif-maker.exe"; Description: "Launch Cove GIF Maker"; Flags: nowait postinstall skipifsilent
