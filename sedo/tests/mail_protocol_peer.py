"""Offline IMAP peer for wire-level tests with Python's real imaplib parser.

Uses stdin/stdout only, never a network socket or real credentials.
"""
import re
import shlex
import sys


def run():
    source, target = sys.stdin.buffer, sys.stdout.buffer
    message = (b'From: Test <kunde@example.org>\r\nTo: info@seller.example.invalid\r\n'
               b'Subject: Echte IMAP-Protokollprobe\r\nMessage-ID: <wire@example.org>\r\n'
               b'Content-Type: text/plain; charset=utf-8\r\n\r\nOffline-Nachricht.\r\n')
    def write(value):
        target.write(value + b'\r\n')
        target.flush()
    write(b'* OK IMAP4rev1 offline peer')
    while line := source.readline():
        try:
            words = shlex.split(line.decode('ascii').strip())
            tag, command = words[:2]
            if command == 'CAPABILITY':
                write(b'* CAPABILITY IMAP4rev1 UIDPLUS MOVE')
            elif command == 'LOGIN':
                pass
            elif command == 'LIST':
                write(b'* LIST (\\HasNoChildren) "/" "INBOX"')
                write(b'* LIST (\\Sent) "/" "Sent"')
            elif command in ('SELECT', 'EXAMINE'):
                write(b'* FLAGS (\\Seen \\Flagged \\Deleted)')
                write(b'* 1 EXISTS')
                write(b'* OK [UIDVALIDITY 9] stable')
            elif command == 'UID':
                operation = words[2]
                if operation == 'SEARCH':
                    write(b'* SEARCH' if 'HEADER' in words else b'* SEARCH 41')
                elif operation == 'FETCH':
                    if b'BODY.PEEK' in line:
                        write(f'* 1 FETCH (UID 41 BODY[] {{{len(message)}}}'.encode())
                        target.write(message)
                        write(b')')
                    else:
                        write(f'* 1 FETCH (UID 41 FLAGS (\\Seen) RFC822.SIZE {len(message)})'.encode())
                elif operation not in ('STORE', 'MOVE', 'EXPUNGE'):
                    raise ValueError('unexpected UID operation')
            elif command == 'APPEND':
                length = int(re.search(rb'\{(\d+)\}', line)[1])
                write(b'+ continue')
                if len(source.read(length)) != length or source.read(2) != b'\r\n':
                    raise ValueError('literal length mismatch')
            elif command == 'LOGOUT':
                write(b'* BYE finished')
                write(tag.encode() + b' OK LOGOUT')
                return
            else:
                raise ValueError('unexpected command')
            write(tag.encode() + b' OK completed')
        except Exception:
            write(line.split(b' ', 1)[0] + b' BAD offline protocol mismatch')


if __name__ == '__main__':
    run()
