import React, { useCallback, useEffect, useState } from 'react';

const AUTH_URL = process.env.REACT_APP_AUTH_URL || 'http://localhost:8001';
const API_URL = process.env.REACT_APP_API_URL || 'http://localhost:8000';

interface AuthUser {
  sub?: string;
  username?: string;
  email?: string;
  name?: string;
}

const ReportPage: React.FC = () => {
  const [initialized, setInitialized] = useState(false);
  const [authenticated, setAuthenticated] = useState(false);
  const [user, setUser] = useState<AuthUser | null>(null);
  const [consentGranted, setConsentGranted] = useState(true);
  const [profile, setProfile] = useState<Record<string, unknown> | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const checkSession = useCallback(async () => {
    try {
      const response = await fetch(`${AUTH_URL}/auth/me`, {
        credentials: 'include',
      });
      if (response.ok) {
        const data = await response.json();
        setAuthenticated(true);
        setUser(data.user ?? null);
        setConsentGranted(Boolean(data.consent_granted));
        setProfile(data.profile ?? null);
      } else {
        setAuthenticated(false);
        setUser(null);
      }
    } catch {
      setAuthenticated(false);
      setUser(null);
    } finally {
      setInitialized(true);
    }
  }, []);

  useEffect(() => {
    checkSession();
  }, [checkSession]);

  const login = () => {
    window.location.href = `${AUTH_URL}/auth/login`;
  };

  const loginYandex = () => {
    window.location.href = `${AUTH_URL}/auth/login/yandex`;
  };

  const logout = async () => {
    await fetch(`${AUTH_URL}/auth/logout`, {
      method: 'POST',
      credentials: 'include',
    });
    setAuthenticated(false);
    setUser(null);
    setConsentGranted(true);
    setProfile(null);
  };

  const acceptConsent = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${AUTH_URL}/auth/consent/accept`, {
        method: 'POST',
        credentials: 'include',
      });
      if (!res.ok) {
        setError('Failed to save consent / profile');
        return;
      }
      const data = await res.json();
      setConsentGranted(true);
      setProfile(data.profile ?? null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Consent error');
    } finally {
      setLoading(false);
    }
  };

  const denyConsent = async () => {
    setLoading(true);
    try {
      await fetch(`${AUTH_URL}/auth/consent/deny`, {
        method: 'POST',
        credentials: 'include',
      });
      setConsentGranted(false);
      setProfile(null);
    } finally {
      setLoading(false);
    }
  };

  const downloadReport = async () => {
    try {
      setLoading(true);
      setError(null);

      const sessionCheck = await fetch(`${AUTH_URL}/auth/session`, {
        credentials: 'include',
      });
      if (!sessionCheck.ok) {
        setError('Not authenticated');
        setAuthenticated(false);
        return;
      }

      const response = await fetch(`${API_URL}/reports`, {
        credentials: 'include',
      });

      if (!response.ok) {
        setError(`Report request failed: ${response.status}`);
        return;
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'An error occurred');
    } finally {
      setLoading(false);
    }
  };

  if (!initialized) {
    return <div>Loading...</div>;
  }

  if (!authenticated) {
    return (
      <div className="flex flex-col items-center justify-center min-h-screen bg-gray-100 gap-3">
        <button
          onClick={login}
          className="px-4 py-2 bg-blue-500 text-white rounded hover:bg-blue-600 w-56"
        >
          Login
        </button>
        <button
          onClick={loginYandex}
          className="px-4 py-2 bg-red-500 text-white rounded hover:bg-red-600 w-56"
        >
          Login with Yandex ID
        </button>
        {error && (
          <div className="mt-4 p-4 bg-red-100 text-red-700 rounded">{error}</div>
        )}
      </div>
    );
  }

  if (!consentGranted) {
    return (
      <div className="flex flex-col items-center justify-center min-h-screen bg-gray-100">
        <div className="p-8 bg-white rounded-lg shadow-md max-w-md">
          <h1 className="text-xl font-bold mb-3">Разрешение на использование данных</h1>
          <p className="text-gray-700 mb-6">
            Сервис протезов BionicPRO запрашивает разрешение использовать данные вашего
            профиля Яндекс ID (имя, email, аватар) и сохранить их в локальной базе.
          </p>
          <div className="flex gap-3">
            <button
              onClick={acceptConsent}
              disabled={loading}
              className="px-4 py-2 bg-green-600 text-white rounded hover:bg-green-700"
            >
              Разрешить
            </button>
            <button
              onClick={denyConsent}
              disabled={loading}
              className="px-4 py-2 bg-gray-400 text-white rounded hover:bg-gray-500"
            >
              Отклонить
            </button>
          </div>
          {error && (
            <div className="mt-4 p-4 bg-red-100 text-red-700 rounded">{error}</div>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col items-center justify-center min-h-screen bg-gray-100">
      <div className="p-8 bg-white rounded-lg shadow-md max-w-lg">
        <h1 className="text-2xl font-bold mb-2">Usage Reports</h1>
        {user?.username && (
          <p className="mb-2 text-gray-600">Signed in as {user.username}</p>
        )}
        {profile && (
          <pre className="mb-6 text-xs bg-gray-50 p-3 rounded overflow-auto max-h-40">
            {JSON.stringify(profile, null, 2)}
          </pre>
        )}

        <div className="flex gap-3">
          <button
            onClick={downloadReport}
            disabled={loading}
            className={`px-4 py-2 bg-blue-500 text-white rounded hover:bg-blue-600 ${
              loading ? 'opacity-50 cursor-not-allowed' : ''
            }`}
          >
            {loading ? 'Generating Report...' : 'Download Report'}
          </button>
          <button
            onClick={logout}
            className="px-4 py-2 bg-gray-500 text-white rounded hover:bg-gray-600"
          >
            Logout
          </button>
        </div>

        {error && (
          <div className="mt-4 p-4 bg-red-100 text-red-700 rounded">{error}</div>
        )}
      </div>
    </div>
  );
};

export default ReportPage;
