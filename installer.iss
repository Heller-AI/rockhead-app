; ============================================================
; Inno Setup script for Rock Head Gradient Analyzer
; ============================================================
; Build: open this file with Inno Setup Compiler and click Compile (or press F9).
; Output: Output\RockHeadApp_Setup.exe
;
; Prerequisites:
;   1. Run build_exe.bat first - it creates dist\RockHeadApp\
;   2. Install Inno Setup from https://jrsoftware.org/isdl.php (free)
; ============================================================

#define MyAppName "Rock Head Gradient Analyzer"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "Your Company Name"
#define MyAppExeName "RockHeadApp.exe"

[Setup]
AppId={{E8B7C2A4-9D5F-4F6A-B8E1-1234567890AB}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\RockHeadApp
DefaultGroupName=Rock Head Analyzer
DisableProgramGroupPage=yes
OutputBaseFilename=RockHeadApp_Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#MyAppExeName}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop icon"; GroupDescription: "Additional shortcuts:"

[Files]
; Bundle everything PyInstaller produced
Source: "dist\RockHeadApp\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Sample Pile Data"; Filename: "{app}\sample_pile_data.csv"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent

[Code]
// Optional: warn user that the app opens in their browser
procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    // Nothing for now - launch is handled by [Run] section
  end;
end;
