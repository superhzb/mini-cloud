<?php
/**
 * DEV / LOOPBACK ONLY — auto-login for the mini-cloud Adminer.
 *
 * Reads the Postgres server/user/password from env (injected by docker-compose from .env) and
 * auto-submits Adminer's own login form, so opening http://127.0.0.1:18432 drops you straight
 * into the database with nothing to type. It preserves Adminer's real auth path (scram-sha-256),
 * it just fills the form for you.
 *
 * This bakes the Postgres superuser password into an auto-submitting page — acceptable only on a
 * loopback bind. It MUST NOT be mounted when INFRA_BIND_ADDR is off loopback or on a VPS.
 */
class AdminerAutoLogin
{
    public function loginForm()
    {
        $server = getenv('ADMINER_DEFAULT_SERVER') ?: 'postgres';
        $user   = getenv('ADMINER_DEFAULT_USER') ?: 'postgres';
        $pass   = getenv('ADMINER_DEFAULT_PASSWORD') ?: '';
        $db     = getenv('ADMINER_DEFAULT_DB') ?: '';
        $h = static function ($s) { return htmlspecialchars((string) $s, ENT_QUOTES); };

        echo '<form action="" method="post" id="mc-autologin">';
        echo '<input type="hidden" name="auth[driver]" value="pgsql">';
        echo '<input type="hidden" name="auth[server]" value="' . $h($server) . '">';
        echo '<input type="hidden" name="auth[username]" value="' . $h($user) . '">';
        echo '<input type="hidden" name="auth[password]" value="' . $h($pass) . '">';
        echo '<input type="hidden" name="auth[db]" value="' . $h($db) . '">';
        echo '<input type="hidden" name="auth[permanent]" value="1">';
        // Fallback if JS is off: a one-click Login button.
        echo '<p style="font:14px sans-serif;padding:1em">mini-cloud dev auto-login → '
           . '<input type="submit" value="Enter ' . $h($server) . '"></p>';
        echo '</form>';
        echo '<script>document.getElementById("mc-autologin").submit();</script>';

        return true; // handled — suppress Adminer's default login form
    }
}

return new AdminerAutoLogin();
