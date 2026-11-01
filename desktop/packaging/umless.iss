; Umless 安装程序（Inno Setup 6）。由 packaging/build.py 调用 ISCC 编译，也可手动：
;   ISCC /DAppVersion=0.3.0 /DSourceDir=..\dist\Umless /DOutputDir=..\dist /DIconFile=..\build\packaging\umless.ico umless.iss
;
; - 按用户安装，不需要管理员权限：%LOCALAPPDATA%\Programs\Umless
; - 用户数据（%APPDATA%\Umless：配置、记录、模型、日志）不在安装目录里，卸载时保留
; - 安装 / 卸载前关闭正在运行的 Umless（常驻进程与主窗口，互斥量 Local\Umless.Core / Local\Umless.UI）
; - 自动更新以 /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /CLOSEAPPLICATIONS 运行，装完自动重新启动 Umless；
;   交互安装在完成页提供「启动 Umless」勾选框

#ifndef AppVersion
  #error 需要用 /DAppVersion=x.y.z 指定版本号（packaging/build.py 会传入）
#endif
#ifndef AppVersionNumeric
  #define AppVersionNumeric AppVersion
#endif
#ifndef SourceDir
  #define SourceDir "..\dist\Umless"
#endif
#ifndef OutputDir
  #define OutputDir "..\dist"
#endif
#ifndef IconFile
  #define IconFile "..\build\packaging\umless.ico"
#endif

#define AppName "Umless"
#define AppExe "Umless.exe"
#define AppURL "https://github.com/despriber/Umless"

; 简体中文语言文件：Inno Setup 6.3 起是官方翻译；旧版本可把 ChineseSimplified.isl 放在本文件旁边
#if FileExists(AddBackslash(CompilerPath) + "Languages\ChineseSimplified.isl")
  #define ZhMessages "compiler:Languages\ChineseSimplified.isl"
#elif FileExists(AddBackslash(SourcePath) + "ChineseSimplified.isl")
  #define ZhMessages "ChineseSimplified.isl"
#else
  #pragma warning "没有找到 ChineseSimplified.isl，安装程序只有英文界面"
#endif

[Setup]
; AppId 固定不变：升级安装靠它识别为同一个程序（{{ 是转义的 {）
AppId={{645066FF-8489-483A-8D56-DDB829AB0061}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=despriber
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
AppUpdatesURL={#AppURL}/releases
VersionInfoVersion={#AppVersionNumeric}
VersionInfoProductVersion={#AppVersionNumeric}
VersionInfoProductTextVersion={#AppVersion}
VersionInfoDescription={#AppName} Setup
PrivilegesRequired=lowest
DefaultDirName={localappdata}\Programs\{#AppName}
DisableDirPage=auto
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
DisableWelcomePage=yes
OutputDir={#OutputDir}
OutputBaseFilename=Umless-Setup-{#AppVersion}
SetupIconFile={#IconFile}
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; 文件被占用时用重启管理器关闭占用的程序；Umless 由 [Code] 自己关闭和重新启动
CloseApplications=force
CloseApplicationsFilter=*.exe,*.dll,*.pyd
RestartApplications=no
SetupMutex=Umless.Setup
ShowLanguageDialog=auto
LanguageDetectionMethod=uilanguage

[Languages]
Name: "en"; MessagesFile: "compiler:Default.isl"
#ifdef ZhMessages
Name: "zh"; MessagesFile: "{#ZhMessages}"
#endif

[CustomMessages]
en.LaunchApp=Launch Umless
en.CloseRunning=Umless is running. Setup will close it before continuing.%n%nClick OK to close Umless, or Cancel to exit Setup.
en.CloseCancelled=Setup was cancelled because Umless is still running.
en.DataKept=Umless has been removed.%n%nYour settings, history and speech models are kept in:%n%1%n%nDelete that folder if you no longer need them.
#ifdef ZhMessages
zh.LaunchApp=启动 Umless
zh.CloseRunning=Umless 正在运行，需要先关闭它才能继续安装。%n%n点「确定」关闭 Umless，点「取消」退出安装。
zh.CloseCancelled=Umless 仍在运行，安装已取消。
zh.DataKept=Umless 已卸载。%n%n你的设置、记录和识别模型仍保留在：%n%1%n%n如果不再需要，可以手动删除这个文件夹。
#endif

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[InstallDelete]
; 升级前清掉旧版本的运行库，避免新旧 DLL 混在一起
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"; WorkingDir: "{app}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
; 交互安装：完成页的「启动 Umless」勾选框
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchApp}"; WorkingDir: "{app}"; Flags: nowait postinstall skipifsilent
; 静默安装（自动更新）：装完直接重新启动
Filename: "{app}\{#AppExe}"; WorkingDir: "{app}"; Flags: nowait; Check: WizardSilent

[UninstallDelete]
; 程序运行时在安装目录里不产生文件；这里只清理残留的空目录
Type: dirifempty; Name: "{app}"

[Code]
const
  UmlessMutexes = 'Local\Umless.Core,Local\Umless.UI';
  RunKey = 'Software\Microsoft\Windows\CurrentVersion\Run';

function UmlessRunning(): Boolean;
begin
  Result := CheckForMutexes(UmlessMutexes);
end;

{ 等 Umless 自己退出（自动更新时它会在启动安装程序后退出），超时后强制结束 }
procedure StopUmless(GraceMs: Integer);
var
  Waited, Code: Integer;
begin
  Waited := 0;
  while UmlessRunning() and (Waited < GraceMs) do
  begin
    Sleep(250);
    Waited := Waited + 250;
  end;
  if not UmlessRunning() then
    Exit;
  Log('Umless 仍在运行，强制结束');
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /T /IM Umless.exe', '', SW_HIDE, ewWaitUntilTerminated, Code);
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /T /IM umless-cli.exe', '', SW_HIDE, ewWaitUntilTerminated, Code);
  Waited := 0;
  while UmlessRunning() and (Waited < 5000) do
  begin
    Sleep(250);
    Waited := Waited + 250;
  end;
  { 仍有互斥量时多半是源码运行的 pythonw 版本：不影响安装目录里的文件，继续安装 }
  if UmlessRunning() then
    Log('Umless 的互斥量仍存在（可能是源码运行的实例），继续安装');
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  if not UmlessRunning() then
    Exit;
  if WizardSilent() then
    StopUmless(10000)
  else
  begin
    if MsgBox(CustomMessage('CloseRunning'), mbConfirmation, MB_OKCANCEL) <> IDOK then
    begin
      Result := CustomMessage('CloseCancelled');
      Exit;
    end;
    StopUmless(0);
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Command: String;
begin
  if CurUninstallStep = usUninstall then
  begin
    if UmlessRunning() then
      StopUmless(0);
  end
  else if CurUninstallStep = usPostUninstall then
  begin
    { 开机自启由程序在设置里开关（HKCU\...\Run 的 Umless 值）：指向本安装目录时一并删除 }
    if RegQueryStringValue(HKCU, RunKey, 'Umless', Command) and
       (Pos(Lowercase(ExpandConstant('{app}')), Lowercase(Command)) > 0) then
      RegDeleteValue(HKCU, RunKey, 'Umless');
    if not UninstallSilent() then
      MsgBox(FmtMessage(CustomMessage('DataKept'), [ExpandConstant('{userappdata}\Umless')]), mbInformation, MB_OK);
  end;
end;
