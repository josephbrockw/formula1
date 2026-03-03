"""
Management command to restore the SQLite database from a backup.

Lists available backups and restores the selected one. The current database
is renamed to db.sqlite3.bak before being replaced, so it can be recovered
if something goes wrong.

Usage:
    python manage.py restore_db --list
    python manage.py restore_db
    python manage.py restore_db --backup db_2025-03-02_21-30-00
    python manage.py restore_db --backup db_2025-03-02_21-30-00 --yes
    python manage.py restore_db --from /Volumes/MyDrive/f1-backups --list
"""

import gzip
import os
import shutil
import tarfile
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = 'Restore the SQLite database from a backup'

    def add_arguments(self, parser):
        parser.add_argument(
            '--from',
            dest='src',
            type=str,
            help='Source backup directory (default: BACKUP_PATH from .env)',
        )
        parser.add_argument(
            '--list',
            action='store_true',
            help='List available backups with timestamps and sizes, then exit',
        )
        parser.add_argument(
            '--backup',
            type=str,
            help='Restore a specific backup by filename stem (e.g. db_2025-03-02_21-30-00)',
        )
        parser.add_argument(
            '--yes',
            action='store_true',
            help='Skip confirmation prompts (for scripting)',
        )
        parser.add_argument(
            '--restore-data',
            action='store_true',
            help='Also restore the matching data/ directory backup',
        )

    def handle(self, *args, **options):
        src_path = self._resolve_src(options['src'])
        backups = self._list_backups(src_path)

        if options['list']:
            self._print_backup_table(backups)
            return

        if not backups:
            raise CommandError(f'No backups found in: {src_path}')

        # Select backup
        if options['backup']:
            chosen = self._find_backup(backups, options['backup'])
        else:
            self._print_backup_table(backups)
            chosen = self._prompt_selection(backups)

        db_backup_path = chosen['db_path']
        data_backup_path = chosen['data_path']

        self.stdout.write(f'\nSelected: {db_backup_path.name}')

        # Confirm
        if not options['yes']:
            confirm = input(
                'This will overwrite the current database. Continue? [y/N] '
            ).strip().lower()
            if confirm != 'y':
                self.stdout.write('Aborted.')
                return

        # Restore database
        db_dest = Path(settings.DATABASES['default']['NAME'])
        bak_dest = db_dest.with_suffix('.sqlite3.bak')

        if db_dest.exists():
            shutil.move(str(db_dest), str(bak_dest))
            self.stdout.write(f'  Previous database saved as: {bak_dest.name}')

        self._decompress_db(db_backup_path, db_dest)
        self.stdout.write(
            self.style.SUCCESS(f'  Restored db.sqlite3 from {db_backup_path.name}')
        )

        # Restore data/ directory
        if data_backup_path and data_backup_path.exists():
            restore_data = options['restore_data']
            if not restore_data and not options['yes']:
                answer = input(
                    f'Also restore data/ directory from {data_backup_path.name}? [y/N] '
                ).strip().lower()
                restore_data = answer == 'y'

            if restore_data:
                data_dest = settings.BASE_DIR / 'data'
                self._restore_data_dir(data_backup_path, data_dest)
                self.stdout.write(
                    self.style.SUCCESS(f'  Restored data/ from {data_backup_path.name}')
                )

        self.stdout.write(
            self.style.WARNING(
                "\nReminder: run 'python manage.py migrate' to apply any pending migrations."
            )
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _resolve_src(self, src_override):
        if src_override:
            path = Path(src_override)
        else:
            env_path = os.getenv('BACKUP_PATH', '').strip()
            if not env_path:
                raise CommandError(
                    'BACKUP_PATH is not set in .env and --from was not provided.\n'
                    'Add BACKUP_PATH=/path/to/backup/dir to your .env file.'
                )
            path = Path(env_path)

        if not path.exists():
            raise CommandError(
                f'Backup source does not exist: {path}\n'
                'Check that the drive is mounted and the directory exists.'
            )
        return path

    def _list_backups(self, src: Path):
        db_files = sorted(
            src.glob('db_*.sqlite3.gz'),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        backups = []
        for db_file in db_files:
            stem = db_file.name[len('db_'):-len('.sqlite3.gz')]
            data_file = src / f'data_{stem}.tar.gz'
            backups.append({
                'stem': stem,
                'db_path': db_file,
                'data_path': data_file if data_file.exists() else None,
                'db_size': db_file.stat().st_size,
            })
        return backups

    def _print_backup_table(self, backups):
        if not backups:
            self.stdout.write('No backups found.')
            return

        self.stdout.write(f'\n{"#":<4} {"Filename stem":<40} {"DB size":<12} {"Data backup"}')
        self.stdout.write('-' * 75)
        for i, b in enumerate(backups, 1):
            data_label = b['data_path'].name if b['data_path'] else '—'
            self.stdout.write(
                f'{i:<4} {b["stem"]:<40} {self._fmt_size(b["db_size"]):<12} {data_label}'
            )
        self.stdout.write('')

    def _find_backup(self, backups, name):
        # Accept stem with or without "db_" prefix, and with or without ".sqlite3.gz" suffix
        stem = name
        if stem.startswith('db_'):
            stem = stem[len('db_'):]
        if stem.endswith('.sqlite3.gz'):
            stem = stem[:-len('.sqlite3.gz')]

        for b in backups:
            if b['stem'] == stem:
                return b

        available = ', '.join(b['stem'] for b in backups)
        raise CommandError(
            f'Backup not found: {name}\nAvailable: {available}'
        )

    def _prompt_selection(self, backups):
        while True:
            choice = input(f'Select backup [1-{len(backups)}]: ').strip()
            try:
                idx = int(choice) - 1
                if 0 <= idx < len(backups):
                    return backups[idx]
            except ValueError:
                pass
            self.stdout.write(f'Please enter a number between 1 and {len(backups)}.')

    def _decompress_db(self, src: Path, dest: Path):
        with gzip.open(str(src), 'rb') as f_in, dest.open('wb') as f_out:
            shutil.copyfileobj(f_in, f_out)

    def _restore_data_dir(self, src: Path, dest: Path):
        if dest.exists():
            shutil.rmtree(dest)
        with tarfile.open(str(src), 'r:gz') as tar:
            tar.extractall(path=str(dest.parent))

    @staticmethod
    def _fmt_size(n_bytes: int) -> str:
        for unit in ('B', 'KB', 'MB', 'GB'):
            if n_bytes < 1024:
                return f'{n_bytes:.1f} {unit}'
            n_bytes /= 1024
        return f'{n_bytes:.1f} TB'
