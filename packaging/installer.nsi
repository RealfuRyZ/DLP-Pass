; ============================================================================
; DLP-Pass Windows 安装包（NSIS 3）
;
; 安装内容：
;   - DLP-Pass 目录版主程序（onedir：DLP-Pass.exe + 运行库）
;   - mappings/（可手动更新的映射数据）
;   - README / LICENSE / CHANGELOG
;   - 当前用户的资源管理器右键菜单（文件、文件夹、文件夹空白处）
;
; 安装范围：程序文件默认安装到 C:\Program Files\DLP-Pass（需管理员权限）；
; 右键菜单、桌面/开始菜单快捷方式和卸载信息注册到当前用户（HKCU）。
; 安装时会单独给 logs/ 目录授予 Users 修改权限，使非提升权限的右键解密
; 也能把日志写入安装目录。
;
; 编译（在 packaging 目录）：
;   makensis /DAPP_VERSION=2.3.2 installer.nsi
;
; 可选编译参数：
;   /DAPP_DIR=DLP-Pass-Win7 /DAPP_EXE=DLP-Pass-Win7.exe
;                                 使用 Win7 兼容版的目录版产物
;   /DOUTPUT_FILE=D:\out\setup.exe 指定安装包输出路径
;
; 请保持本文件为 UTF-8 with BOM，否则中文菜单项可能乱码。
; ============================================================================

Unicode true
!include "MUI2.nsh"
!include "LogicLib.nsh"

!ifndef APP_VERSION
  !define APP_VERSION "2.3.2"
!endif
!ifndef APP_DIR
  !define APP_DIR "DLP-Pass"
!endif
!ifndef APP_EXE
  !define APP_EXE "DLP-Pass.exe"
!endif
!ifndef OUTPUT_FILE
  !define OUTPUT_FILE "dist\DLP-Pass-Setup-${APP_VERSION}.exe"
!endif

!define PRODUCT_NAME "DLP-Pass"
!define PRODUCT_PUBLISHER "DLP-Pass"
!define PRODUCT_URL "https://github.com/RealfuRyZ/DLP-Pass"
!define UNINSTALL_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\DLP-Pass"
!define APP_REG_KEY "Software\DLP-Pass"
!define VERB_FILE "Software\Classes\*\shell\DLPPass.Decrypt"
!define VERB_DIR "Software\Classes\Directory\shell\DLPPass.Decrypt"
!define VERB_BACKGROUND "Software\Classes\Directory\Background\shell\DLPPass.Decrypt"
!define CONTEXT_COMMAND '"$INSTDIR\DLP-Pass.exe" --in-place --progress --notify --shell-batch --decrypt'

Name "${PRODUCT_NAME} ${APP_VERSION}"
Caption "安装 ${PRODUCT_NAME} ${APP_VERSION}"
OutFile "${OUTPUT_FILE}"
InstallDir "$PROGRAMFILES64\DLP-Pass"
; 不读取旧版 InstallDir，避免从旧的 per-user 路径继承默认值
RequestExecutionLevel admin
SetCompressor /SOLID lzma
ManifestDPIAware true
BrandingText "${PRODUCT_NAME} ${APP_VERSION}"

VIProductVersion "${APP_VERSION}.0"
VIAddVersionKey "ProductName" "${PRODUCT_NAME}"
VIAddVersionKey "CompanyName" "${PRODUCT_PUBLISHER}"
VIAddVersionKey "FileDescription" "${PRODUCT_NAME} Windows 安装包"
VIAddVersionKey "FileVersion" "${APP_VERSION}"
VIAddVersionKey "ProductVersion" "${APP_VERSION}"
VIAddVersionKey "LegalCopyright" "GNU GPLv3"
VIAddVersionKey "URLInfoAbout" "${PRODUCT_URL}"

!define MUI_ABORTWARNING
!define MUI_FINISHPAGE_RUN "$INSTDIR\DLP-Pass.exe"
!define MUI_FINISHPAGE_RUN_TEXT "运行 DLP-Pass"

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_COMPONENTS
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "SimpChinese"

; ------------------------------------------------------------------ 主程序
Section "DLP-Pass 主程序" SEC_MAIN
  SectionIn RO
  SetOutPath "$INSTDIR"
  ; onedir 产物：复制整个目录（含 _internal / 运行库）
  File /r "dist\${APP_DIR}\*.*"

  ; Win7 构建的主程序名为 DLP-Pass-Win7.exe，安装后统一为 DLP-Pass.exe；
  ; 右键命令和快捷方式只依赖这个固定名称。
  ${If} "${APP_EXE}" != "DLP-Pass.exe"
    Delete "$INSTDIR\DLP-Pass.exe"
    Rename "$INSTDIR\${APP_EXE}" "$INSTDIR\DLP-Pass.exe"
  ${EndIf}

  ; 映射数据放在 exe 同目录可覆盖内置映射（无需重新构建 exe）
  SetOutPath "$INSTDIR\mappings"
  File "..\mappings\*.json"
  !if /FileExists "..\mappings\README.md"
    File "..\mappings\README.md"
  !endif

  SetOutPath "$INSTDIR"
  !if /FileExists "..\LICENSE"
    File "..\LICENSE"
  !endif
  !if /FileExists "..\README.md"
    File "..\README.md"
  !endif
  !if /FileExists "..\CHANGELOG.md"
    File "..\CHANGELOG.md"
  !endif

  WriteUninstaller "$INSTDIR\Uninstall.exe"
  CreateDirectory "$INSTDIR\logs"
  ; Program Files 默认只有管理员可写；授权 Users 修改 logs/，保证右键解密
  ;（非提升权限）也能直接写日志。失败时程序会自动回退到 %LOCALAPPDATA%。
  nsExec::ExecToLog 'icacls "$INSTDIR\logs" /grant *S-1-5-32-545:(OI)(CI)M /T /C'
  Pop $0
  CreateDirectory "$SMPROGRAMS\DLP-Pass"
  CreateShortCut "$SMPROGRAMS\DLP-Pass\DLP-Pass.lnk" "$INSTDIR\DLP-Pass.exe"
  CreateShortCut "$SMPROGRAMS\DLP-Pass\卸载 DLP-Pass.lnk" "$INSTDIR\Uninstall.exe"
  CreateShortCut "$DESKTOP\DLP-Pass.lnk" "$INSTDIR\DLP-Pass.exe"

  WriteRegStr HKCU "${APP_REG_KEY}" "InstallDir" "$INSTDIR"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayName" "${PRODUCT_NAME}"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayVersion" "${APP_VERSION}"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "Publisher" "${PRODUCT_PUBLISHER}"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "URLInfoAbout" "${PRODUCT_URL}"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayIcon" "$INSTDIR\DLP-Pass.exe,0"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegStr HKCU "${UNINSTALL_KEY}" "QuietUninstallString" '"$INSTDIR\Uninstall.exe" /S'
  WriteRegDWORD HKCU "${UNINSTALL_KEY}" "NoModify" 1
  WriteRegDWORD HKCU "${UNINSTALL_KEY}" "NoRepair" 1
SectionEnd

; ------------------------------------------------------------ 右键菜单
Section "资源管理器右键菜单" SEC_CONTEXT
  ; 文件：%1 为选中的文件路径；MultiSelectModel=Player 允许最多 100 个多选项，
  ; 多选时 Explorer 可能逐项启动命令，CLI 的 --shell-batch 会合并成一个批次。
  WriteRegStr HKCU "${VERB_FILE}" "" "使用 DLP-Pass 解密"
  WriteRegStr HKCU "${VERB_FILE}" "Icon" "$INSTDIR\DLP-Pass.exe,0"
  WriteRegStr HKCU "${VERB_FILE}" "MultiSelectModel" "Player"
  WriteRegStr HKCU "${VERB_FILE}" "Position" "Top"
  WriteRegStr HKCU "${VERB_FILE}\command" "" '${CONTEXT_COMMAND} "%1"'

  ; 文件夹：%1 为选中的文件夹路径。
  WriteRegStr HKCU "${VERB_DIR}" "" "使用 DLP-Pass 解密"
  WriteRegStr HKCU "${VERB_DIR}" "Icon" "$INSTDIR\DLP-Pass.exe,0"
  WriteRegStr HKCU "${VERB_DIR}" "MultiSelectModel" "Player"
  WriteRegStr HKCU "${VERB_DIR}" "Position" "Top"
  WriteRegStr HKCU "${VERB_DIR}\command" "" '${CONTEXT_COMMAND} "%1"'

  ; 文件夹空白处：%V 为当前目录。
  WriteRegStr HKCU "${VERB_BACKGROUND}" "" "使用 DLP-Pass 解密此文件夹"
  WriteRegStr HKCU "${VERB_BACKGROUND}" "Icon" "$INSTDIR\DLP-Pass.exe,0"
  WriteRegStr HKCU "${VERB_BACKGROUND}" "MultiSelectModel" "Player"
  WriteRegStr HKCU "${VERB_BACKGROUND}" "Position" "Top"
  WriteRegStr HKCU "${VERB_BACKGROUND}\command" "" '${CONTEXT_COMMAND} "%V"'

  ; 通知资源管理器刷新关联 / 右键菜单缓存
  System::Call 'shell32::SHChangeNotify(i 0x08000000, i 0, i 0, i 0)'
SectionEnd

; ------------------------------------------------------------ 卸载
Section "Uninstall"
  DeleteRegKey HKCU "${VERB_FILE}"
  DeleteRegKey HKCU "${VERB_DIR}"
  DeleteRegKey HKCU "${VERB_BACKGROUND}"

  Delete "$SMPROGRAMS\DLP-Pass\DLP-Pass.lnk"
  Delete "$SMPROGRAMS\DLP-Pass\卸载 DLP-Pass.lnk"
  RMDir "$SMPROGRAMS\DLP-Pass"
  Delete "$DESKTOP\DLP-Pass.lnk"

  Delete "$INSTDIR\Uninstall.exe"
  RMDir /r "$INSTDIR"

  DeleteRegKey HKCU "${APP_REG_KEY}"
  DeleteRegKey HKCU "${UNINSTALL_KEY}"
  System::Call 'shell32::SHChangeNotify(i 0x08000000, i 0, i 0, i 0)'
SectionEnd

; ------------------------------------------------------------ 组件说明
!insertmacro MUI_FUNCTION_DESCRIPTION_BEGIN
  !insertmacro MUI_DESCRIPTION_TEXT ${SEC_MAIN} "复制 DLP-Pass 目录版主程序、运行库、映射数据和说明文件。"
  !insertmacro MUI_DESCRIPTION_TEXT ${SEC_CONTEXT} "在文件、文件夹和文件夹空白处加入「使用 DLP-Pass 解密」右键菜单。"
!insertmacro MUI_FUNCTION_DESCRIPTION_END
