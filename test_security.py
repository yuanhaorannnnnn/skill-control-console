from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, main
from unittest.mock import patch, Mock
import server

class SecurityTests(TestCase):
    def test_skill_path_blocks_traversal_and_symlink(self):
        for value in ('/tmp/other', '..', '../outside'):
            with self.assertRaises(ValueError): server.Handler._skill_path(value, '', 'self')
        with TemporaryDirectory() as td:
            root = Path(td)
            (root / 'skills').mkdir()
            (root / 'outside').mkdir()
            (root / 'outside' / 'SKILL.md').write_text('test')
            (root / 'skills' / 'link').symlink_to(root / 'outside', target_is_directory=True)
            with patch.object(server, 'AGENT_SKILLS', root / 'skills'):
                self.assertIsNone(server.Handler._skill_path('link', '', 'self'))

    def test_failed_skill_command_reports_error(self):
        handler = object.__new__(server.Handler)
        handler._err = lambda message: {'ok': False, 'error': message}
        handler._ok = lambda value: {'ok': True, **value}
        with patch.object(server.subprocess, 'run', return_value=Mock(returncode=1, stderr='permission denied', stdout='')):
            result = handler._skill_cmd('enable', {'name': 'example', 'upstream': 'repo'})
        self.assertEqual(result, {'ok': False, 'error': 'permission denied'})

if __name__ == '__main__': main()
