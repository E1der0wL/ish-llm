"""불변 DB 세대 복사. Linux 파일시스템이 지원하면 데이터 블록을 공유하고 쓰기 시 분리한다."""

import errno
import os
import shutil


class FileClone:
    READ_MODE = "rb"
    WRITE_MODE = "wb"
    FICLONE = 0x40049409
    COPY_FALLBACK_ERRORS = (errno.EOPNOTSUPP, errno.EXDEV, errno.ENOTTY, errno.EINVAL, errno.ENOSYS)


def copy_file(source, destination):
    import fcntl
    try:
        with open(source, FileClone.READ_MODE) as reader, open(destination, FileClone.WRITE_MODE) as writer:
            fcntl.ioctl(writer.fileno(), FileClone.FICLONE, reader.fileno())
            writer.flush()
            os.fsync(writer.fileno())
        shutil.copystat(source, destination)
    except OSError as error:
        if error.errno not in FileClone.COPY_FALLBACK_ERRORS:
            raise
        shutil.copy2(source, destination)
    return destination
