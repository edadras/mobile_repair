"""Interactive, bilingual (English / فارسی) menu on top of the CLI."""

from __future__ import annotations

import shlex
import sys
from typing import Callable, List, Optional

from .core.context import Context

MENU = [
    ("Device", "دستگاه", [
        ("List devices", "لیست دستگاه‌ها", "devices"),
        ("Doctor (check tools/drivers)", "بررسی ابزارها و درایورها", "doctor"),
        ("Full device info", "اطلاعات کامل دستگاه", "info --full"),
        ("Device info + IMEI", "اطلاعات + IMEI", "info --imei"),
        ("Reboot to recovery", "ریبوت به ریکاوری", "reboot recovery"),
        ("Reboot to bootloader/fastboot", "ریبوت به بوت‌لودر", "reboot bootloader"),
        ("Reboot to fastbootd", "ریبوت به fastbootd", "reboot fastbootd"),
        ("Reboot to EDL", "ریبوت به حالت EDL", "reboot edl"),
        ("Reboot to system", "ریبوت عادی", "reboot system"),
        ("Root shell", "شل روت", "shell --root"),
    ]),
    ("Fastboot", "فست‌بوت", [
        ("getvar all", "خواندن متغیرها", "fastboot getvar all"),
        ("Partition table (fastboot)", "جدول پارتیشن", "fastboot partitions"),
        ("Flash partition", "فلش پارتیشن", "fastboot flash {partition} {image}"),
        ("Boot image temporarily", "بوت موقت ایمیج", "fastboot boot {image}"),
        ("Erase partition", "پاک کردن پارتیشن", "fastboot erase {partition}"),
        ("Unlock bootloader", "آنلاک بوت‌لودر", "fastboot unlock"),
        ("Lock bootloader", "قفل بوت‌لودر", "fastboot lock"),
        ("Set active slot", "تغییر اسلات فعال", "fastboot set-active {slot}"),
        ("Wipe userdata", "پاک کردن userdata", "fastboot wipe"),
        ("OEM command", "دستور OEM", "fastboot oem {args}"),
    ]),
    ("Partitions (dd, root)", "پارتیشن‌ها (dd، روت)", [
        ("List partitions", "لیست پارتیشن‌ها", "part list"),
        ("Dump partition", "دامپ پارتیشن", "part dump {partition} {output}"),
        ("Dump ALL partitions", "دامپ همه پارتیشن‌ها", "part dump-all {directory}"),
        ("Dump byte range", "دامپ محدوده بایتی", "part dump-range {partition} {output} --offset {offset} --length {length}"),
        ("Write image to partition", "نوشتن ایمیج روی پارتیشن", "part write {partition} {image}"),
        ("Compare image with partition", "مقایسه ایمیج با پارتیشن", "part compare {partition} {image}"),
        ("Zero-fill partition", "صفر کردن پارتیشن", "part wipe {partition}"),
    ]),
    ("Backup / Restore", "بکاپ / ری‌استور", [
        ("Backup apps only", "بکاپ برنامه‌ها", "backup create {directory}"),
        ("Backup apps + critical partitions", "بکاپ برنامه‌ها + پارتیشن‌های حیاتی", "backup create {directory} --critical-partitions"),
        ("Backup everything (apps, data, sdcard, partitions)", "بکاپ کامل", "backup create {directory} --app-data --sdcard --all-partitions"),
        ("Restore apps", "ری‌استور برنامه‌ها", "backup restore {directory} --apps"),
        ("Restore partitions", "ری‌استور پارتیشن‌ها", "backup restore {directory} --partitions {partitions}"),
        ("Verify backup", "بررسی سلامت بکاپ", "backup verify {directory}"),
    ]),
    ("ROM / Flash", "فلش رام", [
        ("Inspect ROM package", "شناسایی نوع رام", "rom inspect {path}"),
        ("Flash fastboot image directory", "فلش پوشه ایمیج‌های fastboot", "rom flash-dir {directory}"),
        ("Flash payload.bin / OTA zip", "فلش payload.bin", "rom payload {path}"),
        ("Extract payload.bin only", "استخراج payload.bin", "rom payload {path} --extract-only"),
        ("Sideload zip via recovery", "سایدلود زیپ", "rom sideload {path}"),
        ("fastboot update factory zip", "فلش زیپ فکتوری", "rom update-zip {path}"),
    ]),
    ("Recovery", "ریکاوری", [
        ("Status", "وضعیت", "recovery status"),
        ("Flash custom recovery", "فلش ریکاوری کاستوم", "recovery flash {image} --boot"),
        ("Wipe cache", "پاک کردن کش", "recovery wipe cache"),
        ("Wipe data", "پاک کردن دیتا", "recovery wipe data"),
        ("Factory reset", "بازگشت به تنظیمات کارخانه", "recovery factory-reset"),
        ("Push file to device", "ارسال فایل به دستگاه", "recovery push {path}"),
        ("TWRP command", "دستور TWRP", "recovery twrp {args}"),
    ]),
    ("Apps", "برنامه‌ها", [
        ("List third-party apps", "لیست برنامه‌های نصب‌شده", "apps list --filter third"),
        ("List disabled apps", "لیست برنامه‌های غیرفعال", "apps list --filter disabled"),
        ("App info", "اطلاعات برنامه", "apps info {package}"),
        ("Install APK", "نصب APK", "apps install {path}"),
        ("Uninstall (user 0, no root)", "حذف برنامه", "apps uninstall {package} --user 0"),
        ("Disable app", "غیرفعال کردن", "apps disable {package}"),
        ("Enable app", "فعال کردن", "apps enable {package}"),
        ("Clear app data", "پاک کردن دیتای برنامه", "apps clear {package}"),
        ("Pull APK", "استخراج APK", "apps pull {package}"),
        ("Debloat from list file", "حذف بلوت‌ویر از فایل لیست", "apps debloat {path}"),
    ]),
    ("EFS / NV (IMEI, calibration)", "EFS / NV (آی‌ام‌ای، کالیبراسیون)", [
        ("Detect chipset & partitions", "شناسایی چیپست و پارتیشن‌ها", "efs detect"),
        ("Backup modem NV (atomic)", "بکاپ اتمیک EFS/NV", "efs backup {directory}"),
        ("Restore modem NV (atomic)", "ری‌استور اتمیک EFS/NV", "efs restore {directory}"),
        ("Validate EFS/NV state (deep)", "بررسی وضعیت و ساختار EFS/NV", "efs validate"),
        ("Check dumped images offline", "بررسی آفلاین ایمیج‌های دامپ‌شده", "efs check-image {path}"),
        ("Qualcomm: rebuild modemst from fsg", "کوالکام: بازسازی modemst از fsg", "efs rebuild-modemst"),
        ("MediaTek: rebuild nvdata from nvram backup", "مدیاتک: بازسازی nvdata از بکاپ nvram", "efs mtk-rebuild-nvdata"),
        ("Samsung: fix nv_data md5", "سامسونگ: اصلاح md5", "efs samsung-fix-md5"),
        ("QCN: list NV items", "QCN: لیست آیتم‌های NV", "efs qcn info {path}"),
        ("QCN: extract items", "QCN: استخراج آیتم‌ها", "efs qcn extract {path} --out {directory}"),
        ("QCN: edit an NV item", "QCN: ویرایش آیتم NV", "efs qcn edit {path} {args}"),
        ("Compute NV CRC / md5", "محاسبه CRC/md5", "efs nv-crc --hex {args}"),
    ]),
    ("Logs", "لاگ‌ها", [
        ("Collect full diagnostics bundle", "جمع‌آوری کامل لاگ‌ها", "logs collect"),
        ("Logcat to file", "ذخیره logcat", "logs logcat -o {output}"),
        ("Follow logcat (errors)", "نمایش زنده logcat", "logs logcat -f *:E"),
        ("Kernel log", "لاگ کرنل", "logs dmesg"),
        ("Previous-boot crash log (pstore)", "لاگ کرش بوت قبلی", "logs last-kmsg {directory}"),
        ("Bugreport", "بازارش کامل (bugreport)", "logs bugreport {output}"),
        ("Crash dumps (ANR/tombstones)", "کرش‌ها", "logs crashes {directory}"),
        ("Boot reason", "دلیل بوت/خاموشی", "logs boot-reason"),
    ]),
    ("Screen / Input", "صفحه / ورودی", [
        ("Screenshot", "اسکرین‌شات", "screen shot {output}"),
        ("Record screen", "ضبط صفحه", "screen record {output}"),
        ("Touch test", "تست تاچ", "screen touch-test"),
        ("Wake / unlock", "روشن و آنلاک کردن صفحه", "screen unlock"),
    ]),
]

PROMPTS = {
    "partition": "partition name / نام پارتیشن",
    "image": "image file / مسیر فایل ایمیج",
    "output": "output file / فایل خروجی",
    "directory": "directory / پوشه",
    "path": "file or directory path / مسیر",
    "package": "package name / نام پکیج",
    "slot": "slot (a/b) / اسلات",
    "args": "arguments / آرگومان‌ها",
    "offset": "offset (bytes, e.g. 0, 4K, 0x1000) / آفست",
    "length": "length (bytes) / طول",
    "partitions": "comma separated partitions / پارتیشن‌ها با کاما",
}


def run_menu(ctx: Context, parser, input_fn: Callable[[str], str] = input, out=None) -> None:
    from .cli import dispatch

    out = out or sys.stdout
    print("\nmrt - Mobile Repair Toolkit / ابزار تعمیر و ریکاوری موبایل", file=out)
    print("Type a number, 'b' for back, 'q' to quit. / عدد را وارد کنید، b برای بازگشت، q خروج", file=out)
    while True:
        print("", file=out)
        for i, (en, fa, _) in enumerate(MENU, 1):
            print(f"  {i:2d}) {en:<40} {fa}", file=out)
        choice = input_fn("\nmenu> ").strip().lower()
        if choice in ("q", "quit", "exit"):
            return
        if not choice.isdigit() or not 1 <= int(choice) <= len(MENU):
            continue
        en, fa, items = MENU[int(choice) - 1]
        while True:
            print(f"\n[{en} / {fa}]", file=out)
            for i, (ien, ifa, _) in enumerate(items, 1):
                print(f"  {i:2d}) {ien:<50} {ifa}", file=out)
            sub = input_fn(f"\n{en.lower()}> ").strip().lower()
            if sub in ("b", "back"):
                break
            if sub in ("q", "quit", "exit"):
                return
            if not sub.isdigit() or not 1 <= int(sub) <= len(items):
                continue
            template = items[int(sub) - 1][2]
            argv = _fill(template, input_fn)
            if argv is None:
                continue
            print(f"\n$ mrt {' '.join(shlex.quote(a) for a in argv)}\n", file=out)
            rc = dispatch(parser, argv, ctx)
            print(f"\n[exit code {rc}]", file=out)


def _fill(template: str, input_fn: Callable[[str], str]) -> Optional[List[str]]:
    argv: List[str] = []
    for token in shlex.split(template):
        if token.startswith("{") and token.endswith("}"):
            key = token[1:-1]
            value = input_fn(f"  {PROMPTS.get(key, key)}: ").strip()
            if not value:
                print("  cancelled")
                return None
            if key == "args":
                argv += shlex.split(value)
            else:
                argv.append(value)
        else:
            argv.append(token)
    return argv
