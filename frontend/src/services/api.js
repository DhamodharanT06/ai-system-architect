import axios from 'axios';

// Use relative paths in production (Vercel will rewrite `/api` to the backend).
// When developing locally, set `VITE_API_URL` in `frontend/.env` (e.g. http://localhost:8000).
const API_BASE_URL = import.meta.env.VITE_API_URL ?? '';

export const apiClient = axios.create({
  baseURL: API_BASE_URL,
  headers: {
    'Content-Type': 'application/json',
  },
});

export const generateBlueprint = async (problemStatement, context = null) => {
  try {
    const response = await apiClient.post('/api/generate', {
      problem_statement: problemStatement,
      context: context,
    });
    return response.data;
  } catch (error) {
    console.error('Error generating blueprint:', error);
    throw error;
  }
};

export const getExamples = async () => {
  try {
    const response = await apiClient.get('/api/examples');
    return response.data;
  } catch (error) {
    console.error('Error fetching examples:', error);
    throw error;
  }
};

export const streamBlueprint = (problemStatement, context = null) => {
  const base = API_BASE_URL || '';
  const params = new URLSearchParams({ problem_statement: problemStatement });
  if (context) params.append('context', context);
  const url = `${base}/api/stream-generate?${params.toString()}`;
  return new EventSource(url);
};
