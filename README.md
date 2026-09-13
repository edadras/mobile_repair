# mrt — Mobile Repair Toolkit | ابزار جامع تعمیر و ریکاوری اندروید

<div dir="rtl">

**mrt** یک ابزار خط فرمان جامع برای تعمیرکاران موبایل است که روی `adb` و `fastboot` ساخته شده و
بیشترین دسترسی مجازِ ممکن به دستگاه اندرویدی را در یک ابزار جمع می‌کند. فقط به پایتون (۳.۹ به بالا) و
platform-tools گوگل نیاز دارد؛ هیچ وابستگی خارجی ندارد. همهٔ دستورها هم به صورت خط فرمان و هم از
**منوی تعاملی دوزبانه** (`mrt menu`) در دسترس‌اند.

## امکانات

| بخش | دستور | قابلیت‌ها |
|---|---|---|
| **دستگاه** | `devices`, `doctor`, `info`, `props`, `shell`, `reboot`, `wait` | لیست دستگاه‌های adb/fastboot با حالت؛ بررسی نصب adb/fastboot، درایور و udev؛ خواندن کامل مشخصات (مدل، بورد، کرنل، CPU، حافظه، استوریج، باتری، نمایشگر، شبکه، سیم‌کارت، IMEI با روت، روت/SELinux/Verified Boot/رمزنگاری/FRP/OEM unlock، اسلات A/B، کاربران، همهٔ propها) و ذخیره به JSON؛ شل عادی یا روت؛ ریبوت به هر حالت (system/recovery/bootloader/fastbootd/sideload/EDL/download/safemode) از هر حالتی؛ انتظار برای ظاهر شدن دستگاه در یک حالت |
| **Fastboot** | `fastboot` (`fb`) | `getvar`، جدول پارتیشن بوت‌لودر، فلش، erase، format، بوت موقت ایمیج (تست کرنل/TWRP)، ریبوت، unlock/lock بوت‌لودر (`flashing` یا `oem`، با `--critical`)، تغییر اسلات فعال، دستورات `oem`، `-w`، `continue`، دستور خام |
| **پارتیشن (سطح بیت)** | `part` (`partition`) | لیست پارتیشن‌ها با اندازه و مسیر بلاک؛ نمای کلی بلاک‌دیوایس/GPT/super؛ **دامپ بیت‌به‌بیت با `dd`** (استریم مستقیم با `exec-out`، بدون فایل واسط، تأیید SHA-256 دو طرف)؛ دامپ محدودهٔ بایتی؛ دامپ همهٔ پارتیشن‌ها با `manifest.json`؛ **نوشتن با `dd`** با بررسی اندازه، هش فایل آپلودشده و هش پارتیشن بعد از نوشتن؛ مقایسهٔ ایمیج با پارتیشن؛ صفر کردن پارتیشن |
| **بکاپ / ری‌استور** | `backup` | یک پوشهٔ بکاپ کامل: `device-info.json`، APKهای برنامه‌ها (با split)، دیتای برنامه‌ها (`adb backup`)، حافظهٔ داخلی، ایمیج پارتیشن‌های حیاتی (boot/recovery/dtbo/vbmeta/persist/efs/modemst/fsg/nvram/nvdata/frp/…) یا همهٔ پارتیشن‌ها؛ ری‌استور انتخابی (اپ‌ها، دیتا، پارتیشن‌ها)؛ بررسی سلامت بکاپ با هش |
| **فلش رام** | `rom` | شناسایی نوع بستهٔ رام و روش فلش؛ فلش پوشهٔ ایمیج‌های fastboot با ترتیب درست و جابه‌جایی خودکار به fastbootd برای پارتیشن‌های داینامیک؛ **استخراج و فلش `payload.bin` / OTA zip** (Full OTA، بدون کتابخانهٔ خارجی)؛ لیست محتویات payload؛ `fastboot update` برای factory zip؛ sideload |
| **ریکاوری** | `recovery` (`rec`) | sideload؛ wipe cache/data/dalvik؛ فکتوری‌ریست؛ فلش ریکاوری کاستوم و بوت مستقیم به آن؛ push فایل (رام، Magisk) به دستگاه؛ دستورات TWRP (`install`, `wipe`, `backup`, `restore`)؛ وضعیت حالت فعلی دستگاه‌ها |
| **روت (Magisk)** | `root` | `status`: پیش‌نیازها (ABI، اسلات، `boot` یا `init_boot`، قفل بوت‌لودر، روت موجود، اپ Magisk، fastboot یا Odin)؛ `patch`: پچ ایمیج بوتِ همان بیلد (از فایل، از `payload.bin` رام یا دامپ از دستگاه روت‌شده) با خودِ `boot_patch.sh` مجیسک روی دستگاه و ذخیرهٔ ایمیج اصلی + پچ‌شده + manifest با هش؛ `flash`: ریبوت به بوت‌لودر، رد کردن بوت‌لودر قفل، بررسی هش و مدل، فلش دائم یا بوت موقت (`--temporary`)؛ `install`: پچ + فلش؛ `unroot`: بازگردانی ایمیج اصلی |
| **EFS / NV (هویت مودم)** | `efs` (`nv`) | شناسایی چیپست و پارتیشن‌های موجود؛ **بکاپ/ری‌استور اتمیک** گروه (Qualcomm: modemst1/modemst2/fsg/fsc با مدیریت آینه، MediaTek: nvram/nvdata/nvcfg/protect1/2، Samsung: efs/sec_efs + فایل‌سیستم `/efs`) با محافظ مدل دستگاه و rollback خودکار؛ `validate`: وضعیت erased/آینه و **بررسی ساختار داخلی** (سوپربلاک‌های EFS2 و سنِ نسخهٔ زنده، سوپربلاک ext4 و CRC32C، هدر بکاپ nvram)؛ `check-image`: همان بررسی آفلاین روی دامپ‌ها؛ `rebuild-modemst`: بازسازی modemst1/2 از fsg توسط خودِ مودم (کوالکام)؛ `mtk-rebuild-nvdata`: بازسازی nvdata از بکاپ nvram توسط nvram_daemon (مدیاتک)؛ `samsung-fix-md5`: بازمحاسبهٔ `nv_data.bin.md5`؛ `nv-crc`: CRC-16/X-25 و MD5؛ **QCN**: لیست/استخراج/ویرایش آفلاین آیتم‌های NV و مقایسهٔ دو فایل |
| **مدیریت اپ** | `apps` (`app`, `pm`) | لیست (third/system/disabled)؛ اطلاعات کامل بسته؛ نصب (split APK)؛ حذف (کاربر ۰ بدون روت)؛ فعال/غیرفعال؛ پاک کردن دیتا؛ force stop؛ استخراج APK؛ بکاپ/ری‌استور دیتای یک اپ؛ اعطا/لغو مجوز؛ **حذف بلوت‌ویر گروهی** از فایل لیست؛ پروسس‌های در حال اجرا |
| **لاگ و تشخیص** | `logs` (`log`) | logcat (فایل یا زنده با فیلتر)؛ dmesg؛ pstore/last_kmsg (کرش بوت قبلی)؛ bugreport؛ dumpsys؛ ANR/tombstone/dropbox؛ آمار باتری؛ دلیل آخرین بوت/خاموشی؛ **بستهٔ کامل تشخیصی** در یک پوشه |
| **صفحه** | `screen` | اسکرین‌شات، ضبط ویدیو، تزریق tap/swipe/کلید/متن، تست خام تاچ، بیدار/آنلاک کردن با PIN |

هر دستوری که روی میزبان اجرا می‌شود، همراه با کد خروجی، مدت زمان و هش‌ها در **لاگ عملیات** (`~/.mrt/logs/`) به صورت متنی و JSONL ثبت می‌شود.

### ایمنی

* عملیات مخرب تأیید `y/N` می‌خواهد و عملیات حیاتی (نوشتن روی پارتیشن، unlock/lock، wipe، فلش رام، ری‌استور EFS، بازسازی modemst/nvdata) نیاز به تایپ **نام پارتیشن یا توکن** دارد (`UNLOCK`, `RESTORE`, `REBUILD`, `MISMATCH`, …).
* قبل از هر نوشتن `dd`، اندازهٔ ایمیج با اندازهٔ پارتیشن مقایسه می‌شود، فایل آپلودشده روی دستگاه هش می‌شود و **فقط اگر هش برابر بود** نوشتن انجام می‌شود؛ بعد از نوشتن هم پارتیشن دوباره هش و مقایسه می‌شود.
* ری‌استور EFS و بازسازی modemst/nvdata اول از محتوای فعلی بکاپ (rollback) می‌گیرند و اگر بکاپ مربوط به مدل دیگری باشد یا نسخهٔ مبدأ (fsg / بکاپ nvram) معتبر نباشد، اجرا نمی‌شوند.
* روت: بوت‌لودر قفل، ایمیج دست‌خورده (هش نامطابق) یا مدل متفاوت، فلش را متوقف می‌کند.
* `--dry-run` همهٔ دستورات را چاپ می‌کند بدون این که اجرا شوند. `--yes` تأییدها را رد می‌کند (فقط برای اسکریپت).
* پارتیشن‌های حساس (EFS، modemst، persist، frp، …) به‌طور پیش‌فرض در فلش خودکار رام لمس نمی‌شوند.

### نصب

</div>

```bash
git clone https://github.com/edadras/mobile_repair.git
cd mobile_repair
pip install -e .           # فقط کتابخانهٔ استاندارد پایتون (>=3.9)؛ وابستگی خارجی ندارد
mrt doctor                 # بررسی adb / fastboot / درایور / دستگاه
```

<div dir="rtl">

اگر `adb`/`fastboot` ندارید: `scripts/get-platform-tools.sh` (لینوکس/مک) یا از [platform-tools گوگل](https://developer.android.com/tools/releases/platform-tools) نصب کنید. در لینوکس فایل `scripts/51-android.rules` را در `/etc/udev/rules.d/` کپی کنید. مسیر دلخواه: `--adb/--fastboot` یا متغیرهای `MRT_ADB`/`MRT_FASTBOOT`.

### گزینه‌های سراسری

</div>

| گزینه | معنی |
|---|---|
| `-s, --serial` | انتخاب دستگاه (سریال adb یا fastboot) |
| `-y, --yes` | پذیرش خودکار تأییدها (فقط اسکریپت) |
| `-n, --dry-run` | فقط چاپ دستورات میزبان |
| `--json` | خروجی JSON |
| `-v, --verbose` | نمایش هر دستور اجراشده |
| `--log-dir`, `--no-log` | محل لاگ عملیات (`~/.mrt/logs`, `$MRT_LOG_DIR`) |
| `--no-adb-root` | هرگز `adb root` نزن، فقط `su` |

<div dir="rtl">

### منوی تعاملی (دوزبانه)

</div>

```bash
mrt menu
```

<div dir="rtl">

### نمونه دستورها

</div>

```bash
# اطلاعات کامل + IMEI و ذخیره در فایل
mrt info --full --imei --save report.json
mrt reboot edl

# پارتیشن‌ها و دامپ بیت‌به‌بیت (روت)
mrt part list
mrt part dump boot boot_backup.img          # دامپ + تأیید SHA-256 روی دستگاه و میزبان
mrt part dump-all ./dump --max-size 512M     # همهٔ پارتیشن‌های کوچک + manifest.json
mrt part dump-range boot gpt.bin --offset 0 --length 0x4400
mrt part write boot boot_backup.img          # نوشتن با dd، تأیید قبل و بعد
mrt part compare boot boot_backup.img

# بکاپ کامل
mrt backup create backups/customer1 --app-data --sdcard --critical-partitions
mrt backup verify backups/customer1
mrt backup restore backups/customer1 --apps --partitions persist,efs

# فست‌بوت و رام
mrt fastboot getvar all
mrt fastboot unlock
mrt rom inspect firmware.zip
mrt rom payload-info firmware.zip
mrt rom payload firmware.zip --extract-only --workdir imgs
mrt rom flash-dir imgs --slot a --wipe --reboot
mrt rom sideload ota.zip

# ریکاوری
mrt recovery flash twrp.img --boot
mrt recovery push Magisk.apk
mrt recovery twrp install /sdcard/Magisk.zip
mrt recovery wipe cache

# روت با Magisk (بوت‌لودر باید آنلاک باشد؛ ایمیج بوت از همان بیلد نصب‌شده)
mrt root status
mrt root patch --magisk Magisk-v27.0.apk --boot-image init_boot.img
mrt root patch --magisk Magisk-v27.0.apk --payload firmware.zip   # ایمیج اصلی از payload رام
mrt root flash backups/root-<time> --temporary                    # تست یک‌باره بدون فلش
mrt root install --magisk Magisk-v27.0.apk --boot-image boot.img  # پچ + فلش
mrt root unroot backups/root-<time>

# EFS / NV (هویت مودم: IMEI، MAC، کالیبراسیون) - نیاز به روت
mrt efs detect
mrt efs backup backups/efs-c1                 # بکاپ اتمیک گروه (Qualcomm/MTK/Samsung خودکار)
mrt efs validate                              # وضعیت آینه modemst1/2، ساختار EFS2/ext4/nvram و md5 سامسونگ
mrt efs check-image backups/efs-c1            # همان بررسی، آفلاین روی دامپ‌ها
mrt efs restore backups/efs-c1                # ری‌استور اتمیک با rollback خودکار
mrt efs rebuild-modemst --reboot              # Qualcomm: مودم modemst1/2 را از fsg بازسازی می‌کند
mrt efs mtk-rebuild-nvdata --reboot           # MediaTek: nvram_daemon nvdata را از بکاپ nvram برمی‌گرداند
mrt efs samsung-fix-md5                       # اصلاح nv_data.bin.md5 سامسونگ
mrt efs qcn info backup.qcn                   # لیست آیتم‌های NV داخل QCN
mrt efs qcn edit backup.qcn 550 --value-ascii "12345"   # ویرایش آفلاین آیتم NV
mrt efs qcn diff old.qcn new.qcn

# برنامه‌ها، لاگ‌ها، صفحه
mrt apps list --filter third
mrt apps debloat scripts/debloat-example.txt --mode uninstall
mrt logs collect ./diag
mrt logs last-kmsg ./crash
mrt logs logcat -f '*:E'
mrt screen shot shot.png
mrt screen unlock --pin 1234
```

<div dir="rtl">

مرجع کامل همهٔ دستورها و گزینه‌ها: [`docs/COMMANDS.md`](docs/COMMANDS.md) — نکات ایمنی و سناریوهای تعمیر (بوت‌لوپ، فلش رام، روت، EFS/IMEI): [`docs/REPAIR-GUIDE.md`](docs/REPAIR-GUIDE.md)

### چک‌سام‌ها: چه چیزی واقعی است

* `nv_data.bin.md5` سامسونگ یک چک‌سام واقعی و قابل بازمحاسبه است (`efs samsung-fix-md5`).
* سوپربلاک EFS2 در `modemst`/`fsg` کوالکام **هیچ فیلد CRC ندارد** و `nvdata`/`nvram` مدیاتک ext4 و ناحیهٔ بکاپ nvram_daemon هستند؛ چک‌سام دستی برای آن‌ها وجود ندارد. ابزار ساختار آن‌ها را بررسی می‌کند و برای تعمیر، خودِ فریم‌ور را وادار به بازسازی می‌کند (`rebuild-modemst` / `mtk-rebuild-nvdata`) یا گروه را اتمیک ری‌استور می‌کند.
* CRC-16 آیتم‌های NV فقط روی خط DIAG است و داخل فایل QCN ذخیره نمی‌شود؛ `qcn edit` بدون نیاز به «اصلاح چک‌سام» کار می‌کند.

### محدودیت‌ها

* عملیات سطح پارتیشن با `dd` و EFS نیاز به **روت** (Magisk/`su` یا `adb root` روی بیلدهای userdebug) دارد. بدون روت از `fastboot` استفاده کنید.
* پروتکل‌های اختصاصی (Odin/Samsung، EDL/Firehose کوالکام، MTK SP Flash، Huawei) و پروتکل DIAG پیاده‌سازی نشده‌اند؛ ابزار فقط تا حالت EDL/Download ریبوت می‌کند و QCN را آفلاین ویرایش می‌کند (نوشتن NV روی مودم با QPST/QFIL).
* روت روی سامسونگ: `root patch` ایمیج پچ‌شده را می‌سازد اما فلش باید با Odin (AP tar) انجام شود. فایل APK مجیسک را خودتان از انتشار رسمی بگیرید؛ ابزار چیزی دانلود نمی‌کند.
* `payload.bin`های **تفاضلی** (incremental OTA) قابل استخراج نیستند، فقط Full OTA.
* `adb backup` در اندرویدهای جدید منسوخ شده و برای اپ‌هایی که `allowBackup=false` دارند کار نمی‌کند.

### توسعه

</div>

```bash
pip install -e .[dev]
pytest                     # تست‌ها با runner جعلی اجرا می‌شوند؛ به دستگاه نیاز ندارند
```

---

## English

**mrt** is a stdlib-only Python CLI for phone repair technicians. It wraps `adb` and `fastboot`
to give the maximum access a device permits, from the command line or a bilingual interactive menu (`mrt menu`).

| area | command | what it does |
|---|---|---|
| Device | `devices`, `doctor`, `info`, `props`, `shell`, `reboot`, `wait` | list adb/fastboot devices; check tools/drivers; complete readout (identity, kernel, CPU, memory, storage, battery, display, network, SIM, IMEI with root, root/SELinux/verified boot/encryption/FRP/OEM unlock, A/B slots) saved as JSON; normal or root shell; reboot to any mode (recovery, bootloader, fastbootd, sideload, EDL, download, safemode) from any mode |
| Fastboot | `fastboot` | getvar, partition table, flash, erase, format, temporary boot, unlock/lock (`flashing`/`oem`, `--critical`), set-active, oem, `-w`, continue, raw |
| Partitions | `part` | list, block/super layout, bit-by-bit `dd` dump streamed over `adb exec-out` and sha256-verified on both sides, byte-range dump, dump-all with manifest, verified `dd` write (size + upload hash + post-write hash), compare, wipe |
| Backup | `backup` | one directory with device info, APKs (splits), app data (`adb backup`), internal storage, critical or all partition images; selective restore; hash verification |
| ROM | `rom` | identify a package; flash fastboot image dirs in the right order with automatic fastbootd switch; extract/flash `payload.bin` / full OTA zips (no external libs); `fastboot update`; sideload |
| Recovery | `recovery` | sideload, wipe, factory reset, flash custom recovery and boot it, push files, TWRP commands, mode status |
| Root | `root` | `status` (ABI, slot, `boot` vs `init_boot`, bootloader lock, existing root, Magisk app); `patch` the stock image of the installed build (file, OTA payload, or dump from a rooted device) with Magisk's own `boot_patch.sh` on the device; `flash` (refuses a locked bootloader, checks image hash and product; `--temporary` = one-time `fastboot boot`); `install` = patch + flash; `unroot` |
| EFS / NV | `efs` | chipset detection; atomic backup/restore of the modem identity group (Qualcomm modemst1/modemst2/fsg/fsc with mirror handling, MediaTek nvram/nvdata/nvcfg/protect, Samsung efs + `/efs`) with device guard and rollback; `validate` / `check-image` verify the internal structure (EFS2 superblocks and live age, ext4 superblock + CRC32C, nvram backup header); `rebuild-modemst` (modem rebuilds modemst from fsg) and `mtk-rebuild-nvdata` (nvram_daemon restores nvdata from the nvram backup); Samsung `nv_data.bin.md5` fix; CRC-16/X-25 + MD5; offline QCN list/extract/edit/diff |
| Apps | `apps` | list, info, install (splits), uninstall (user 0, no root), enable/disable, clear, stop, pull APK, per-app backup/restore, grant/revoke permissions, bulk debloat from a list, processes |
| Logs | `logs` | logcat, dmesg, pstore/last_kmsg, bugreport, dumpsys, ANR/tombstones/dropbox, battery stats, boot reason, full diagnostics bundle |
| Screen | `screen` | screenshot, record, tap/swipe/key/text injection, raw touch test, wake + unlock |

Every host command is written to an operation log (`~/.mrt/logs/*.log` + `*.jsonl`).

```bash
pip install -e .
mrt doctor                       # check adb/fastboot, drivers, devices
mrt menu                         # interactive bilingual menu
mrt info --full                  # everything about the device
mrt part dump boot boot.img      # dd | verified with sha256 on both sides
mrt part write boot boot.img     # size check + upload hash + dd + post-write hash
mrt rom payload ota.zip --extract-only
mrt backup create --critical-partitions --app-data
mrt root install --magisk Magisk.apk --boot-image boot.img
mrt efs backup backups/efs && mrt efs validate
mrt logs collect ./diag
```

Safety: destructive commands ask `y/N`; brick-capable commands require typing the partition
name or a token (`UNLOCK`, `RESTORE`, `REBUILD`, `MISMATCH`, ...). EFS restore and the rebuild
commands take a rollback/backup first and refuse when the source copy does not verify; root
flashing refuses a locked bootloader, a tampered image or a different product. `--dry-run`
prints commands without running them; `--yes` skips prompts for scripting.
See [`docs/COMMANDS.md`](docs/COMMANDS.md) for the full reference and
[`docs/REPAIR-GUIDE.md`](docs/REPAIR-GUIDE.md) for repair scenarios.

Requirements: Python ≥ 3.9, Android platform-tools on `PATH` (or `--adb/--fastboot`, `MRT_ADB`/`MRT_FASTBOOT`).
Partition-level and EFS operations need root on the device (Magisk `su` or `adb root`).
Not implemented: vendor protocols (Odin, EDL/Firehose, SP Flash, DIAG); incremental OTA payloads.

Development: `pip install -e .[dev] && pytest` (tests use a scripted fake runner; no device needed).

License: MIT.
