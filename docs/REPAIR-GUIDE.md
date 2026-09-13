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
```

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
