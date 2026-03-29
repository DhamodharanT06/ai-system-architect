import axios from 'axios';

const API_BASE_URL = import.meta.env.VITE_API_URL || '';

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
  const baseUrl = API_BASE_URL || window.location.origin;
  const url = new URL('/api/stream-generate', baseUrl);
  url.searchParams.append('problem_statement', problemStatement);
  if (context) {
    url.searchParams.append('context', context);
  }
  
  return new EventSource(url.toString());
};
