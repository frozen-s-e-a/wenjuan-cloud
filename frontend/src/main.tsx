import { createRoot } from 'react-dom/client';
import AnswerPage from './AnswerPage';
import AdminPage from './AdminPage';
import './style.css';
createRoot(document.getElementById('root')!).render(location.pathname === '/admin' ? <AdminPage /> : <AnswerPage />);
