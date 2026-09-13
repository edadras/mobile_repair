# Repair scenarios / سناریوهای تعمیر

<div dir="rtl">

## قبل از هر کاری: بکاپ

</div>

```bash
mrt info --full --save before.json
mrt backup create backups/<customer> --critical-partitions --app-data     # root: EFS/modem/persist/NV + APKs + app data
mrt part dump-all backups/<customer>/all --max-size 1G                    # every small partition, hash-verified
```

<div dir="rtl">

پارتیشن‌های **EFS / modemst1 / modemst2 / fsg / nvram / nvdata / persist / proinfo** حاوی IMEI، MAC و کالیبراسیون هستند. هرگز بدون دامپ آن‌ها فلش نکنید. این پارتیشن‌ها در `rom flash-dir` به‌طور پیش‌فرض لمس نمی‌شوند.

## بوت‌لوپ / Bootloop

</div>

1. `mrt reboot recovery` → `mrt logs last-kmsg ./crash` (needs root/TWRP) — kernel panic text from the previous boot.
2. `mrt logs logcat -o boot.txt` while it loops (adb comes up briefly on many devices).
3. Try the other slot on A/B devices: `mrt fastboot getvar current-slot`, `mrt fastboot set-active b`.
4. Reflash boot only: `mrt fastboot flash boot boot.img` (from `mrt rom payload fw.zip --extract-only --only boot`).
5. Wipe cache/dalvik: `mrt recovery wipe cache`; last resort `mrt recovery wipe data`.

<div dir="rtl">

## فلش رام رسمی

</div>

```bash
mrt rom inspect firmware.zip                     # tells you which flow applies
mrt fastboot unlock                              # if needed (erases data!)
mrt rom payload firmware.zip --slot a --wipe --reboot     # A/B OTA zips
mrt rom flash-dir ./images --reboot              # fastboot image folders (Xiaomi/Pixel style)
mrt rom sideload ota.zip                         # recovery-flashable / stock OTA
```

<div dir="rtl">

## ریکاوری کاستوم و روت

</div>

```bash
mrt fastboot boot twrp.img          # test first without flashing
mrt recovery flash twrp.img --boot  # then flash
mrt recovery push Magisk.apk        # then install from TWRP, or:
mrt recovery twrp install /sdcard/Magisk.zip

# root without TWRP (Magisk boot-image patching, like the app but from the host):
mrt root status                                                   # unlocked? boot or init_boot? ABI?
mrt fastboot unlock                                               # once; erases all data
mrt root patch --magisk Magisk-v27.0.apk --boot-image init_boot.img   # stock image of the INSTALLED build
mrt root flash backups/root-<time> --temporary                    # test once without flashing
mrt root flash backups/root-<time>                                # flash for good, then open the Magisk app
mrt root unroot backups/root-<time>                               # back to stock
```

<div dir="rtl">

* ایمیج بوت باید دقیقاً از همان بیلدِ نصب‌شده باشد (از فایل رام یا با `--payload` از OTA). ایمیج بیلد دیگر = بوت‌لوپ.
* روی دستگاه‌های Android 13+ با پارتیشن `init_boot`، همان را پچ کنید (`root status` خودش تشخیص می‌دهد).
* سامسونگ fastboot ندارد: `root patch` ایمیج پچ‌شده را می‌سازد، اما فلش باید با Odin (داخل AP tar) انجام شود.
* بوت‌لودر قفل باشد، ایمیج پچ‌شده بوت نمی‌شود؛ `root flash` در این حالت کار را متوقف می‌کند.

</div>

<div dir="rtl">

## اپ‌های سیستمی مزاحم (بدون روت)

</div>

```bash
mrt apps list --filter system --grep facebook
mrt apps debloat scripts/debloat-example.txt --mode uninstall   # pm uninstall -k --user 0
mrt apps debloat com.example.bloat --mode reinstall              # undo
```

<div dir="rtl">

## تشخیص سخت‌افزار

</div>

```bash
mrt info                        # battery health/level, sensors (--full), display size
mrt screen touch-test           # raw touch events: dead zones show as missing coordinates
mrt logs dumpsys batterystats
mrt logs collect ./diag         # send the folder to a colleague
```

<div dir="rtl">

## نکات ایمنی نوشتن روی پارتیشن

* `mrt part write` فقط وقتی می‌نویسد که ایمیج از پارتیشن بزرگ‌تر نباشد و هش فایل آپلودشده با فایل محلی برابر باشد؛ بعد از نوشتن دوباره هش می‌گیرد.
* ایمیج کوچک‌تر از پارتیشن مجاز است (مثل boot.img)؛ با `--exact-size` می‌توانید سخت‌گیر باشید.
* در دستگاه‌های A/B نام بدون پسوند (`boot`) به اسلات فعلی نگاشت می‌شود. برای اسلات دیگر صریحاً `boot_b` بنویسید.
* روی `super`/`userdata` که در حال استفاده‌اند از ریکاوری (TWRP) بنویسید، نه از سیستم بوت‌شده.
* برای پارتیشن‌های داینامیک (system/vendor/product) در حالت fastboot باید در **fastbootd** باشید؛ `rom flash-dir` این کار را خودکار انجام می‌دهد.

</div>

<div dir="rtl">

## EFS / NV و هویت مودم (IMEI/سیم شناسایی نمی‌شود)

**قبل از هر کاری** گروه EFS/NV را اتمیک بکاپ بگیرید (روت لازم است):

</div>

```bash
mrt efs detect                       # چیپست و پارتیشن‌های موجود
mrt efs backup backups/efs-<sn>      # Qualcomm: modemst1+modemst2+fsg+fsc | MTK: nvram+nvdata+nvcfg+protect | Samsung: efs + /efs
mrt efs validate                     # آیا modemst1/2 خالی/erased شده؟ آینه‌ها یکسان‌اند؟ ساختار داخلی (EFS2 / ext4 / nvram) سالم است؟
mrt efs check-image backups/efs-<sn> # همان بررسی ساختار، آفلاین روی دامپ‌ها
mrt efs rebuild-modemst              # Qualcomm: پاک کردن modemst1/2 تا مودم آن‌ها را از fsg بازسازی کند (بعد از بکاپ و بررسی fsg)
mrt efs mtk-rebuild-nvdata           # MediaTek: خالی کردن nvdata تا nvram_daemon آن را از بکاپ nvram بازسازی کند (بعد از بکاپ و بررسی nvram)
```

<div dir="rtl">

**بازگردانی بعد از خرابی EFS (سیم‌کارت شناسایی نمی‌شود، IMEI صفر):**

</div>

```bash
mrt efs restore backups/efs-<sn>     # همهٔ گروه با هم (مدیریت آینه + rollback خودکار)، سپس ریبوت
```

<div dir="rtl">

**نکات مهم و صادقانه:**

* `modemst1` و `modemst2` یک **جفت آینه** هستند و `fsg` نسخهٔ کارخانه است. اگر فقط یکی را برگردانید، مودم ممکن است از دیگری بازسازی کند و تغییر شما گم شود؛ ابزار در این حالت هشدار می‌دهد. همیشه کل گروه را با هم برگردانید.
* برای `modemst`/`fsg` (EFS2) و `nvram`/`nvdata` (بکاپ nvram_daemon / ext4) **چک‌سامی که با دست بازمحاسبه شود وجود ندارد**: سوپربلاک EFS2 اصلاً فیلد CRC ندارد و ext4 چک‌سام‌هایش را خود کرنل می‌نویسد. «بازمحاسبهٔ چک‌سام داخلی» در عمل یعنی وادار کردن خودِ فریم‌ور به بازسازی: `efs rebuild-modemst` مودم کوالکام را وامی‌دارد modemst1/2 را از `fsg` بسازد و `efs mtk-rebuild-nvdata` باعث می‌شود nvram_daemon مدیاتک nvdata را از بکاپ `nvram` برگرداند. هر دو اول بکاپ می‌گیرند و اگر نسخهٔ مبدأ (fsg / بکاپ nvram) معتبر نباشد، اجرا نمی‌شوند. `efs validate` و `efs check-image` ساختار داخلی (سوپربلاک‌ها، سن (age) نسخهٔ زنده، هندسه، هدر بکاپ) را بررسی می‌کنند. در غیر این صورت راه درست، بازگردانی اتمیک از دامپ سالمِ **همان دستگاه** است.
* برای **سامسونگ**، بعد از دستکاری `/efs`، سایدکار md5 را اصلاح کنید وگرنه مودم `nv_data.bin` را رد می‌کند:

</div>

```bash
mrt efs samsung-fix-md5              # روی دستگاه (روت) - یا --local DIR روی یک کپی
```

<div dir="rtl">

**کار با QCN (بکاپ QPST):** خواندن/استخراج/ویرایش **آفلاین** پشتیبانی می‌شود، اما **نوشتن QCN روی مودم از طریق adb ممکن نیست** و به پورت DIAG و QPST/QFIL نیاز دارد.

</div>

```bash
mrt efs qcn info modem.qcn
mrt efs qcn extract modem.qcn --out qcn_items
mrt efs qcn edit modem.qcn 550 --value 00112233 --out modem_new.qcn
mrt efs qcn diff modem.qcn modem_new.qcn
```

<div dir="rtl">

### IMEI (لایهٔ سرویس)

`mrt imei` بسته به چیپست مسیر درست را انتخاب می‌کند: کوالکام از DIAG روی NV/QCN، مدیاتک از META روی NVRAM/NVDATA، سامسونگ از AT/سرویس روی EFS/NV.

* خواندن: `mrt imei read` (زنده) یا `mrt imei qcn-read backup.qcn` (آفلاین از آیتم NV 550).
* اعتبارسنجی: `mrt imei check 3532831100000xx` — رقم کنترل Luhn را بررسی/تکمیل می‌کند.
* بازگردانی IMEI *خودِ دستگاه* بعد از تعویض برد: `mrt imei qcn-write backup.qcn <imei>` سپس QCN را با QPST روی مودم بازگردانید. توکن: `IMEI`.
* نوشتن زنده: `mrt imei plan-write <imei>` فریم DIAG یا فرمان `AT+EGMR` را می‌سازد؛ ارسال آن نیازمند پورت اختصاصی (QPST/QFIL، META، یا پورت AT مودم) است و از adb ممکن نیست.

نوشتن IMEI فقط برای بازگردانی شمارهٔ اصلی همان دستگاه مجاز است؛ ابزار هرگز شمارهٔ جعلی نمی‌سازد.

</div>
