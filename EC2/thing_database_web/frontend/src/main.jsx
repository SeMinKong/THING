// frontend/src/main.jsx
import React from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';

import App from './App.jsx';
import './index.css';

createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    {/* Vue의 createWebHistory()에 대응. Nginx의 try_files ... /index.html 설정과 짝을 이룬다 */}
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </React.StrictMode>
);
