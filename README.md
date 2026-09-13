# mrt — Mobile Repair Toolkit | ابزار جامع تعمیر و ریکاوری اندروید

<div dir="rtl">

**mrt** یک ابزار خط فرمان جامع برای تعمیرکاران موبایل است که روی `adb` و `fastboot` ساخته شده و
بیشترین دسترسی مجازِ ممکن به دستگاه اندرویدی را در یک ابزار جمع می‌کند:

| بخش | قابلیت‌ها |
|---|---|
| **اطلاعات** | خواندن کامل مشخصات: مدل، بورد، بیس‌بند، بوت‌لودر، وضعیت قفل/Verified Boot، رمزنگاری، روت، SELinux، اسلات A/B، سیم‌کارت، IMEI (با روت)، باتری، حافظه، CPU، شبکه، FRP، همهٔ propها |
| **Fastboot** | `getvar`، جدول پارتیشن، فلش، erase/format، بوت موقت، unlock/lock بوت‌لودر، تغییر اسلات، `oem`، `-w`، دستور خام |
| **پارتیشن (سطح بیت)** | لیست پارتیشن‌ها با اندازه، **دامپ بیت‌به‌بیت با `dd`** (استریم مستقیم با `exec-out`، بدون فایل واسط)، دامپ محدودهٔ بایتی، **نوشتن با `dd`** با بررسی اندازه و SHA-256 قبل و بعد از نوشتن، مقایسه، صفر کردن، دامپ همهٔ پارتیشن‌ها با manifest |
| **بکاپ/ری‌استور** | APKها (با split)، دیتای برنامه‌ها (`adb backup`)، حافظهٔ داخلی، ایمیج پارتیشن‌های حیاتی (EFS/modem/persist/NV/boot…) یا همهٔ پارتیشن‌ها؛ ری‌استور و بررسی سلامت با هش |
| **فلش رام** | شناسایی نوع رام، فلش پوشهٔ ایمیج‌های fastboot با ترتیب درست و جابه‌جایی خودکار به fastbootd، **استخراج و فلش `payload.bin`** (بدون کتابخانهٔ خارجی)، `fastboot update`، sideload |
| **ریکاوری** | ریبوت به هر حالت (recovery/bootloader/fastbootd/sideload/EDL/download)، sideload، wipe cache/data، فکتوری‌ریست، فلش ریکاوری کاستوم، push فایل، دستورات TWRP |
| **روت (Magisk)** | بررسی پیش‌نیازها (قفل بوت‌لودر، ABI، boot/init_boot)، پچ کردن ایمیج بوت با خودِ `boot_patch.sh` مجیسک روی دستگاه (از فایل، از `payload.bin` رام یا دامپ دستگاه روت‌شده)، فلش دائم یا بوت موقت با fastboot، حذف روت با بازگردانی ایمیج اصلی |
| **EFS / NV** | شناسایی چیپست، بکاپ/ری‌استور **اتمیک** گروه هویت مودم (Qualcomm: modemst1/modemst2/fsg/fsc با مدیریت آینه، MediaTek: nvram/nvdata/nvcfg/protect، Samsung: efs + فایل‌سیستم /efs)، بررسی ساختار داخلی EFS2 / ext4 / بکاپ nvram و بازسازی modemst از fsg (کوالکام) یا nvdata از بکاپ nvram (مدیاتک) توسط خود فریم‌ور، بازمحاسبهٔ `nv_data.bin.md5` سامسونگ، خواندن/استخراج/ویرایش آفلاین آیتم‌های NV در فایل **QCN**، محاسبهٔ CRC-16/X-25 و MD5 |
| **مدیریت اپ** | لیست/اطلاعات/نصب/حذف/غیرفعال/فعال/پاک کردن دیتا/استخراج APK/بکاپ‌وری‌استور دیتا/مجوزها/حذف بلوت‌ویر گروهی بدون روت |
| **لاگ** | logcat (فایل یا زنده)، dmesg، pstore/last_kmsg (لاگ کرش بوت قبلی)، bugreport، dumpsys، ANR/tombstone/dropbox، دلیل بوت، بستهٔ کامل تشخیصی |
| **صفحه** | اسکرین‌شات، ضبط، تزریق لمس/کلید/متن، تست تاچ، بیدار/آنلاک کردن |

هر دستوری که روی میزبان اجرا می‌شود، همراه با کد خروجی، مدت زمان و هش‌ها در **لاگ عملیات** (`~/.mrt/logs/`) به صورت متنی و JSONL ثبت می‌شود.

### ایمنی

* عملیات مخرب تأیید `y/N` می‌خواهد و عملیات حیاتی (نوشتن روی پارتیشن، unlock/lock، wipe، فلش رام) نیاز به تایپ **نام پارتیشن یا توکن** دارد.
* قبل از هر نوشتن `dd`، اندازهٔ ایمیج با اندازهٔ پارتیشن مقایسه می‌شود، فایل آپلودشده روی دستگاه هش می‌شود و **فقط اگر هش برابر بود** نوشتن انجام می‌شود؛ بعد از نوشتن هم پارتیشن دوباره هش و مقایسه می‌شود.
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

اگر `adb`/`fastboot` ندارید: `scripts/get-platform-tools.sh` (لینوکس/مک) یا از [platform-tools گوگل](https://developer.android.com/tools/releases/platform-tools) نصب کنید. در لینوکس فایل `scripts/51-android.rules` را در `/etc/udev/rules.d/` کپی کنید.

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

# پارتیشن‌ها و دامپ بیت‌به‌بیت
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
mrt rom payload firmware.zip --extract-only --workdir imgs
mrt rom flash-dir imgs --slot a --wipe --reboot
mrt rom sideload ota.zip

# ریکاوری، برنامه‌ها، لاگ‌ها
mrt reboot edl
mrt recovery flash twrp.img --boot
mrt apps debloat scripts/debloat-example.txt --mode uninstall
mrt logs collect ./diag
mrt logs last-kmsg ./crash
mrt logs logcat -f '*:E'
```

# EFS / NV (هویت مودم: IMEI، MAC، کالیبراسیون) - نیاز به روت
mrt efs detect
mrt efs backup backups/efs-c1                 # بکاپ اتمیک گروه (Qualcomm/MTK/Samsung خودکار)
mrt efs validate                              # وضعیت آینه modemst1/2، ساختار EFS2/ext4/nvram و md5 سامسونگ
mrt root status                               # پیش‌نیازهای روت
mrt root install --magisk Magisk.apk --boot-image boot.img   # پچ + فلش با Magisk
mrt efs restore backups/efs-c1                # ری‌استور اتمیک با rollback خودکار
mrt efs samsung-fix-md5                        # اصلاح nv_data.bin.md5 سامسونگ
mrt efs qcn info backup.qcn                    # لیست آیتم‌های NV داخل QCN
mrt efs qcn edit backup.qcn 550 --value-ascii "12345"   # ویرایش آفلاین آیتم NV
```

<div dir="rtl">

مرجع کامل همهٔ دستورها: [`docs/COMMANDS.md`](docs/COMMANDS.md) — نکات ایمنی و سناریوهای تعمیر: [`docs/REPAIR-GUIDE.md`](docs/REPAIR-GUIDE.md)

### محدودیت‌ها

* عملیات سطح پارتیشن با `dd` نیاز به **روت** (Magisk/`su` یا `adb root` روی بیلدهای userdebug) دارد. بدون روت از `fastboot` استفاده کنید.
* پروتکل‌های اختصاصی (Odin/Samsung، EDL/Firehose کوالکام، MTK SP Flash، Huawei) پیاده‌سازی نشده‌اند؛ ابزار فقط تا حالت EDL/Download ریبوت می‌کند.
* `payload.bin`های **تفاضلی** (incremental OTA) قابل استخراج نیستند، فقط Full OTA.
* `adb backup` در اندرویدهای جدید منسوخ شده و برای اپ‌هایی که `allowBackup=false` دارند کار نمی‌کند.

</div>

---

## English

**mrt** is a stdlib-only Python CLI for phone repair technicians. It wraps `adb` and `fastboot`
to give the maximum access a device permits: complete information readout, fastboot operations,
raw bit-by-bit partition read/write with `dd` (streamed over `adb exec-out`, hash-verified),
backup/restore, ROM flashing (fastboot image dirs, `payload.bin`/OTA zips, factory zips, sideload),
recovery helpers, app management and log collection. Every host command is written to an
operation log (`~/.mrt/logs/*.log` + `*.jsonl`).

```bash
pip install -e .
mrt doctor                       # check adb/fastboot, drivers, devices
mrt menu                         # interactive bilingual menu
mrt info --full                  # everything about the device
mrt part dump boot boot.img      # dd | verified with sha256 on both sides
mrt part write boot boot.img     # size check + upload hash + dd + post-write hash
mrt rom payload ota.zip --extract-only
mrt backup create --critical-partitions --app-data
mrt logs collect ./diag
```

Safety: destructive commands ask `y/N`; brick-capable commands require typing the partition
name or a token (`UNLOCK`, `FLASH`, `WIPE`...). `--dry-run` prints commands without running them;
`--yes` skips prompts for scripting. See [`docs/COMMANDS.md`](docs/COMMANDS.md) for the full
reference and [`docs/REPAIR-GUIDE.md`](docs/REPAIR-GUIDE.md) for repair scenarios.

Requirements: Python ≥ 3.9, Android platform-tools on `PATH` (or `--adb/--fastboot`, `MRT_ADB`/`MRT_FASTBOOT`).
Partition-level operations need root on the device (Magisk `su` or `adb root`).

Development: `pip install -e .[dev] && pytest` (tests use a scripted fake runner; no device needed).

License: MIT.
