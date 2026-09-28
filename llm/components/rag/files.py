"""불변 DB 세대 복사. Linux 파일시스템이 지원하면 데이터 블록을 공유하고 쓰기 시 분리한다."""

import errno
import os
import shutil


def copy_file(source, destination):
    import fcntl
    try:
        with open(source, "rb") as reader, open(destination, "wb") as writer:
            fcntl.ioctl(writer.fileno(), 0x40049409, reader.fileno())  # Linux FICLONE
            writer.flush()
            os.fsync(writer.fileno())
        shutil.copystat(source, destination)
    except OSError as error:
        if error.errno not in (errno.EOPNOTSUPP, errno.EXDEV, errno.ENOTTY, errno.EINVAL, errno.ENOSYS):
            raise
        shutil.copy2(source, destination)
    return destination
