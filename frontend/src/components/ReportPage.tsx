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
    // Redirect browser to BFF — tokens never touch the frontend
    window.location.href = `${AUTH_URL}/auth/login`;
  };

  const logout = async () => {
    await fetch(`${AUTH_URL}/auth/logout`, {
      method: 'POST',
      credentials: 'include',
    });
    setAuthenticated(false);
    setUser(null);
  };

  const downloadReport = async () => {
    try {
      setLoading(true);
      setError(null);

      // Session cookie is sent automatically (credentials: 'include')
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
      <div className="flex flex-col items-center justify-center min-h-screen bg-gray-100">
        <button
          onClick={login}
          className="px-4 py-2 bg-blue-500 text-white rounded hover:bg-blue-600"
        >
          Login
        </button>
        {error && (
          <div className="mt-4 p-4 bg-red-100 text-red-700 rounded">{error}</div>
        )}
      </div>
    );
  }

  return (
    <div className="flex flex-col items-center justify-center min-h-screen bg-gray-100">
      <div className="p-8 bg-white rounded-lg shadow-md">
        <h1 className="text-2xl font-bold mb-2">Usage Reports</h1>
        {user?.username && (
          <p className="mb-6 text-gray-600">Signed in as {user.username}</p>
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
