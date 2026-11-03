; ALFABE SATIŞ PRO kurulum betiği (Inno Setup 6).  Sürüm numarası hazirla.py ile programdan okunur.
#include "surum.iss"

[Setup]
; DİKKAT: AppId sabit kalmalı; aynı kimlik sayesinde yeni sürüm eskisinin üzerine (güncelleme) kurulur.
AppId={{00D9A401-ACA5-47B5-9327-E6A72516FC2B}
AppName=ALFABE SATIŞ PRO
AppVersion={#AppVersion}
AppVerName=ALFABE SATIŞ PRO {#AppVersionText}
AppPublisher=ALFABE KIRTASİYE
DefaultDirName={autopf}\ALFABE SATIS PRO
DefaultGroupName=ALFABE SATIŞ PRO
; KTS kurulumundaki gibi: Başlat Menüsü klasörü sayfası ve "Başlat Menüsü klasörü oluşturulmasın" kutusu
DisableProgramGroupPage=no
AllowNoIcons=yes
DisableWelcomePage=no
DisableReadyPage=no
DisableDirPage=no
InfoBeforeFile=KURULUM_BILGILERI.txt
WizardImageFile=kurulum_yan.bmp
WizardSmallImageFile=kurulum_kucuk.bmp
OutputDir=..\KURULUM_CIKTI
OutputBaseFilename=ALFABE_SATIS_PRO_Kurulum_{#AppVersion}
SetupIconFile=..\alfabe.ico
UninstallDisplayIcon={app}\ALFABE_SATIS_PRO.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
; Program çalışırken kurulum/kaldırma uyarı verir (programdaki mutex ile aynı ad):
AppMutex=ALFABE_SATIS_PRO_SINGLE
; Not: 64 bit Python ile paketlediyseniz aşağıdaki satırın başındaki ; işaretini kaldırın.
;ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "turkish"; MessagesFile: "compiler:Languages\Turkish.isl"

[Messages]
WelcomeLabel2=[name/ver] bilgisayarınıza kurulacak.%n%nDevam etmeden önce çalışan diğer programları kapatmanız önerilir.%n%nNot: Güncelleme amacıyla kuruyorsanız ve ALFABE SATIŞ PRO şu anda çalışıyorsa, ilerlemeden önce kapatın. Verileriniz silinmez.

[Tasks]
Name: "desktopicon"; Description: "Masaüstüne kısayol oluştur"; GroupDescription: "Ek görevler:"

[Dirs]
; Ortak/özel veri klasörü ayarı (ag_ayari.json) bu klasöre yazılır; tüm Windows kullanıcıları okuyabilsin.
Name: "{commonappdata}\ALFABE_SATIS_PRO"; Permissions: users-modify

[InstallDelete]
; Güncellemede eski sürümden kalan paket dosyaları temizlenir. Kullanıcı verisi burada DEĞİLDİR (LOCALAPPDATA).
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "..\dist\ALFABE_SATIS_PRO\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\ALFABE SATIŞ PRO"; Filename: "{app}\ALFABE_SATIS_PRO.exe"
Name: "{group}\ALFABE SATIŞ PRO Kaldır"; Filename: "{uninstallexe}"
Name: "{autodesktop}\ALFABE SATIŞ PRO"; Filename: "{app}\ALFABE_SATIS_PRO.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\ALFABE_SATIS_PRO.exe"; Description: "ALFABE SATIŞ PRO'yu başlat"; Flags: nowait postinstall skipifsilent

[Code]
// Kaldırma sonrası: firma/dönem verileri varsayılan olarak SİLİNMEZ. Yalnızca kullanıcı isterse silinir.
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Veri: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    Veri := ExpandConstant('{localappdata}\ALFABE_SATIS_PRO');
    if DirExists(Veri) then
      if MsgBox('Firma, dönem ve yedek verileri şu klasörde duruyor:' + #13#10 + Veri + #13#10#13#10 +
                'Bu veriler de SİLİNSİN mi?' + #13#10 + '(Emin değilseniz HAYIR deyin; program yeniden kurulursa verileriniz kullanılabilir.)',
                mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
        DelTree(Veri, True, True, True);
  end;
end;

var
  VeriSayfasi: TInputQueryWizardPage;

procedure InitializeWizard;
begin
  VeriSayfasi := CreateInputQueryPage(wpSelectTasks, 'Veri Klasörü', 'Bu bilgisayar verileri nerede tutsun?',
    'TEK bilgisayarda çalışacaksanız bu alanı BOŞ bırakın (veriler bu bilgisayarda tutulur).' + #13#10 + #13#10 +
    'BİRDEN ÇOK bilgisayarda ortak çalışacaksanız ortak veri klasörünü yazın:' + #13#10 +
    '  • Diğer bilgisayarlarda:   \\SUNUCU\ALFABE_VERI' + #13#10 +
    '  • Sunucu bilgisayarın kendisinde (yerel yol):   D:\ALFABE_VERI' + #13#10 + #13#10 +
    'Daha önce ayarlanmışsa ve boş bırakırsanız mevcut ayar korunur. Sonradan programdaki "Ağ / Veri Ayarı" ile de değiştirilebilir.');
  VeriSayfasi.Add('Ortak veri klasörü:', False);
end;

function JsonYol(S: String): String;
begin
  Result := S;
  StringChangeEx(Result, '\', '\\', True);
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Yol, Dosya: String;
begin
  if CurStep = ssPostInstall then
  begin
    Yol := Trim(VeriSayfasi.Values[0]);
    if Yol <> '' then
    begin
      Dosya := ExpandConstant('{commonappdata}\ALFABE_SATIS_PRO\ag_ayari.json');
      ForceDirectories(ExtractFileDir(Dosya));
      SaveStringToFile(Dosya, '{"data_dir": "' + JsonYol(Yol) + '"}', False);
      if Copy(Yol, 1, 2) <> '\\' then ForceDirectories(Yol);
    end;
  end;
end;
