"""
Management command to back up the SQLite database and data/ directory.

Creates timestamped, compressed backups on an external drive (or any directory).
Uses SQLite's online backup API so the backup is safe while Django is running.

Usage:
    python manage.py backup_db
    python manage.py backup_db --dest /Volumes/MyDrive/f1-backups
    python manage.py backup_db --keep 5
    python manage.py backup_db --tag before-2024-import
"""

import gzip
import os
import shutil
import sqlite3
import tarfile
import tempfile
import time
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = 'Back up the SQLite database and data/ directory to a backup location'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dest',
            type=str,
            help='Override destination directory (default: BACKUP_PATH from .env)',
        )
        parser.add_argument(
            '--keep',
            type=int,
            default=10,
            help='Number of most-recent backups to retain (default: 10)',
        )
        parser.add_argument(
            '--tag',
            type=str,
            help='Optional label appended to filenames (e.g. "before-2024-import")',
        )

    def handle(self, *args, **options):
        start = time.time()

        dest_path = self._resolve_dest(options['dest'])
        keep = options['keep']
        tag = options['tag']

        timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
        stem = f'{timestamp}_{tag}' if tag else timestamp

        db_backup_name = f'db_{stem}.sqlite3.gz'
        data_backup_name = f'data_{stem}.tar.gz'

        db_backup_path = dest_path / db_backup_name
        data_backup_path = dest_path / data_backup_name

        # --- Back up database ---
        db_src = settings.DATABASES['default']['NAME']
        if not Path(db_src).exists():
            raise CommandError(f'Database file not found: {db_src}')

        self.stdout.write('Backing up database...')
        db_bytes = self._backup_db(Path(db_src), db_backup_path)
        self.stdout.write(
            self.style.SUCCESS(f'  Backed up: {db_backup_name} ({self._fmt_size(db_bytes)})')
        )

        # --- Back up data/ directory ---
        data_dir = settings.BASE_DIR / 'data'
        if data_dir.exists():
            self.stdout.write('Backing up data/ directory...')
            data_bytes = self._backup_data_dir(data_dir, data_backup_path)
            self.stdout.write(
                self.style.SUCCESS(
                    f'  Backed up: {data_backup_name} ({self._fmt_size(data_bytes)})'
                )
            )
        else:
            self.stdout.write(self.style.WARNING('  Skipping data/ (directory not found)'))

        # --- Prune old backups ---
        retained = self._prune_backups(dest_path, keep)

        elapsed = time.time() - start
        self.stdout.write(
            f'\nBackups retained: {retained}/{keep} | Elapsed: {elapsed:.1f}s'
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _resolve_dest(self, dest_override):
        if dest_override:
            path = Path(dest_override)
        else:
            env_path = os.getenv('BACKUP_PATH', '').strip()
            if not env_path:
                raise CommandError(
                    'BACKUP_PATH is not set in .env and --dest was not provided.\n'
                    'Add BACKUP_PATH=/path/to/backup/dir to your .env file.'
                )
            path = Path(env_path)

        if not path.exists():
            raise CommandError(
                f'Backup destination does not exist: {path}\n'
                'Check that the drive is mounted and the directory exists.'
            )
        return path

    def _backup_db(self, src: Path, dest: Path) -> int:
        """Back up the SQLite database using the online backup API, then gzip it."""
        with tempfile.NamedTemporaryFile(suffix='.sqlite3', delete=False) as tmp:
            tmp_path = Path(tmp.name)

        try:
            # Online backup: safe to run while Django is active
            src_conn = sqlite3.connect(str(src))
            dst_conn = sqlite3.connect(str(tmp_path))
            with dst_conn:
                src_conn.backup(dst_conn)
            src_conn.close()
            dst_conn.close()

            # Gzip the snapshot
            with tmp_path.open('rb') as f_in, gzip.open(str(dest), 'wb') as f_out:
                shutil.copyfileobj(f_in, f_out)
        finally:
            tmp_path.unlink(missing_ok=True)

        return dest.stat().st_size

    def _backup_data_dir(self, data_dir: Path, dest: Path) -> int:
        """Tar + gzip the data/ directory."""
        with tarfile.open(str(dest), 'w:gz') as tar:
            tar.add(str(data_dir), arcname='data')
        return dest.stat().st_size

    def _prune_backups(self, dest: Path, keep: int) -> int:
        """Delete oldest db_*.sqlite3.gz files (and matching data_*.tar.gz) beyond --keep."""
        db_backups = sorted(dest.glob('db_*.sqlite3.gz'), key=lambda p: p.stat().st_mtime)

        to_delete = db_backups[:-keep] if len(db_backups) > keep else []

        for db_file in to_delete:
            stem = db_file.name[len('db_'):-len('.sqlite3.gz')]
            data_file = dest / f'data_{stem}.tar.gz'

            db_file.unlink(missing_ok=True)
            if data_file.exists():
                data_file.unlink()

        retained = min(len(db_backups), keep)
        return retained

    @staticmethod
    def _fmt_size(n_bytes: int) -> str:
        for unit in ('B', 'KB', 'MB', 'GB'):
            if n_bytes < 1024:
                return f'{n_bytes:.1f} {unit}'
            n_bytes /= 1024
        return f'{n_bytes:.1f} TB'
