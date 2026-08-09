import axios from 'axios';

// 개발 환경이면 localhost, 빌드된 배포 환경이면 /api 상대경로를 사용합니다.
const baseURL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000/api';

const api = axios.create({
  baseURL: baseURL,
  timeout: 5000,
  withCredentials: true,
  headers: {
    'Content-Type': 'application/json',
  }
});

export default api;
