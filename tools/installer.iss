; QQ聊天记录导出 - Inno Setup 6
#define MyAppName "绮录"
#ifdef SmokeBuild
  #undef MyAppName
  #define MyAppName "QQExport Smoke Test"
  #define MyAppId "QQExportSmokeTests"
#else
  #define MyAppId "{{8E3D2C7A-5B1E-4A2B-9B8E-2E6F6A1C4D10}"
#endif
#ifndef MyAppVersion
  #error MyAppVersion must be supplied by tools/build_release.py
#endif
#ifndef SourceDir
  #define SourceDir "..\dist\QQExport"
#endif
#define MyAppExeName "QQExport.exe"

[Setup]
AppId={#MyAppId}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=qingzhi-star
DefaultDirName={localappdata}\Programs\QQExport
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=..\dist\installer
OutputBaseFilename=QQExport-Setup-{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
SetupIconFile=..\qqexport\assets\qilu.ico
PrivilegesRequired=lowest
UninstallDisplayIcon={app}\{#MyAppExeName}
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "chinesesimp"; MessagesFile: "ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务:"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "启动 {#MyAppName}"; Flags: nowait postinstall skipifsilent
