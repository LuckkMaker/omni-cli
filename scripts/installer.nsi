; OMNI CLI NSIS 安装器
; 由 scripts/make-installer.ps1 调用，需通过 /D 传入 VERSION / OUTFILE / BUILD_DIR / SCRIPT_DIR。
;
; 安装形态与 scripts/cli-install/install.ps1 一致：
;   %LOCALAPPDATA%\Programs\OMNI CLI\bin\omni.exe   + 用户 PATH
; 另带：应用与功能里的卸载入口、静默安装/卸载（/S）。

Unicode true

!include "MUI2.nsh"
!include "LogicLib.nsh"
!include "WinMessages.nsh"

!ifndef VERSION
  !define VERSION "0.0.0"
!endif
!ifndef OUTFILE
  !define OUTFILE "OMNI-CLI-setup.exe"
!endif
!ifndef BUILD_DIR
  !define BUILD_DIR "."
!endif
!ifndef SCRIPT_DIR
  !define SCRIPT_DIR "."
!endif

!define CLI_BIN "$INSTDIR\bin"
!define REG_APP "Software\OMNI\CLI"
!define REG_UNINST "Software\Microsoft\Windows\CurrentVersion\Uninstall\OMNI CLI"
!define HELPER "$INSTDIR\omni-path.ps1"
!define POWERSHELL "$SYSDIR\WindowsPowerShell\v1.0\powershell.exe"

Name "OMNI CLI ${VERSION}"
OutFile "${OUTFILE}"
InstallDir "$LOCALAPPDATA\Programs\OMNI CLI"
InstallDirRegKey HKCU "${REG_APP}" "InstallDir"
RequestExecutionLevel user
SetCompressor /SOLID lzma
SetCompressorDictSize 32
BrandingText "OMNI CLI ${VERSION}"

!define MUI_ABORTWARNING
!define MUI_FINISHPAGE_NOAUTOCLOSE

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_LICENSE "${SCRIPT_DIR}\..\LICENSE"
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

!insertmacro MUI_LANGUAGE "SimpChinese"
!insertmacro MUI_LANGUAGE "English"

Section "OMNI CLI" SecMain
  SectionIn RO

  SetOutPath "${CLI_BIN}"
  File "${BUILD_DIR}\omni.exe"

  SetOutPath "$INSTDIR"
  File /oname=omni-path.ps1 "${SCRIPT_DIR}\installer-path.ps1"
  File /oname=README.md "${SCRIPT_DIR}\..\README.md"
  File /oname=LICENSE "${SCRIPT_DIR}\..\LICENSE"

  WriteUninstaller "$INSTDIR\Uninstall.exe"

  ; 用户 PATH：交给随包 PowerShell 助手做幂等增删（避免 NSIS 字符串处理的长脚本）
  nsExec::ExecToStack '"${POWERSHELL}" -NoProfile -ExecutionPolicy Bypass -File "${HELPER}" add "${CLI_BIN}"'
  Pop $0
  ${If} $0 != "0"
    DetailPrint "警告：写入用户 PATH 失败（$0），可手动把 ${CLI_BIN} 加入 PATH。"
  ${Else}
    SendMessage ${HWND_BROADCAST} ${WM_SETTINGCHANGE} 0 "STR:Environment" /TIMEOUT=5000
  ${EndIf}

  WriteRegStr HKCU "${REG_APP}" "InstallDir" "$INSTDIR"

  WriteRegStr HKCU "${REG_UNINST}" "DisplayName" "OMNI CLI"
  WriteRegStr HKCU "${REG_UNINST}" "DisplayVersion" "${VERSION}"
  WriteRegStr HKCU "${REG_UNINST}" "Publisher" "LuckkMaker"
  WriteRegStr HKCU "${REG_UNINST}" "DisplayIcon" "${CLI_BIN}\omni.exe"
  WriteRegStr HKCU "${REG_UNINST}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${REG_UNINST}" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegStr HKCU "${REG_UNINST}" "QuietUninstallString" '"$INSTDIR\Uninstall.exe" /S'
  WriteRegDWORD HKCU "${REG_UNINST}" "NoModify" 1
  WriteRegDWORD HKCU "${REG_UNINST}" "NoRepair" 1
SectionEnd

Section "Uninstall"
  ; 先移除 PATH（助手还在），再删文件
  nsExec::ExecToStack '"${POWERSHELL}" -NoProfile -ExecutionPolicy Bypass -File "${HELPER}" remove "${CLI_BIN}"'
  Pop $0
  ${If} $0 == "0"
    SendMessage ${HWND_BROADCAST} ${WM_SETTINGCHANGE} 0 "STR:Environment" /TIMEOUT=5000
  ${EndIf}

  Delete "${CLI_BIN}\omni.exe"
  Delete "$INSTDIR\omni-path.ps1"
  Delete "$INSTDIR\README.md"
  Delete "$INSTDIR\LICENSE"
  Delete "$INSTDIR\Uninstall.exe"
  RMDir "${CLI_BIN}"
  RMDir "$INSTDIR"

  DeleteRegKey HKCU "${REG_UNINST}"
  DeleteRegKey HKCU "${REG_APP}"
SectionEnd
