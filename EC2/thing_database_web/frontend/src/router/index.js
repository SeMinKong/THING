// frontend/src/router/index.js
import { createRouter, createWebHistory } from 'vue-router';
import HomeView from '../views/HomeView.vue';
import DataDownloadView from '../views/DataDownloadView.vue';

const routes = [
  { path: '/', name: 'home', component: HomeView },
  { path: '/download', name: 'download', component: DataDownloadView },
];

const router = createRouter({
  history: createWebHistory(),
  routes,
});

export default router;