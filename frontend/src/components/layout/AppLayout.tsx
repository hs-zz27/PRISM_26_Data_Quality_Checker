import React from 'react';
import { Outlet } from 'react-router-dom';
import { Navbar } from './Navbar';
import { Sidebar } from './Sidebar';
import { GlobalJobTray } from './GlobalJobTray';

export const AppLayout: React.FC = () => {
  return (
    <div className="app-frame">
      <Navbar />
      <div className="app-body">
        <Sidebar />
        <main className="app-content">
          <Outlet />
        </main>
      </div>
      <GlobalJobTray />
    </div>
  );
};
