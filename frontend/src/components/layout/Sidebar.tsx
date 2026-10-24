import React, { useState } from 'react';
import { NavLink } from 'react-router-dom';
import { BookOpen, Database, SearchCode, Wrench, DownloadCloud, TrendingUp, Pin, LayoutDashboard } from 'lucide-react';

const NAV_ITEMS = [
  { to: '/', label: 'Overview', icon: LayoutDashboard },
  { to: '/rulebooks', label: 'Rulebooks', icon: BookOpen },
  { to: '/datasets', label: 'Datasets', icon: Database },
  { to: '/audits', label: 'Audits', icon: SearchCode },
  { to: '/corrections', label: 'Corrections', icon: Wrench },
  { to: '/exports', label: 'Exports', icon: DownloadCloud },
  { to: '/feedback', label: 'Feedback', icon: TrendingUp },
];

export const Sidebar: React.FC = () => {
  const [pinned, setPinned] = useState(() => localStorage.getItem('prism-nav-pinned') === 'true');
  const togglePinned = () => setPinned((current) => {
    localStorage.setItem('prism-nav-pinned', String(!current));
    return !current;
  });

  return (
    <aside className={`app-rail${pinned ? ' is-pinned' : ''}`} aria-label="Main navigation">
      <div className="rail-brand" aria-label="PRISM">
        <span className="prism-mark" aria-hidden="true"><i /><i /><i /></span>
        {pinned && <span className="rail-brand-name">PRISM</span>}
      </div>
      <nav className="rail-nav">
        {NAV_ITEMS.map(({ to, label, icon: Icon }) => (
          <NavLink key={to} to={to} end={to === '/'} title={label} aria-label={label}
            className={({ isActive }) => `rail-link${isActive ? ' is-active' : ''}`}>
            <Icon size={17} strokeWidth={1.6} aria-hidden="true" />
            {pinned && <span>{label}</span>}
          </NavLink>
        ))}
      </nav>
      <button className={`rail-pin${pinned ? ' is-active' : ''}`} onClick={togglePinned}
        aria-label={pinned ? 'Collapse navigation' : 'Pin expanded navigation'} title={pinned ? 'Collapse navigation' : 'Pin navigation'}>
        <Pin size={15} strokeWidth={1.6} />
        {pinned && <span>Pin navigation</span>}
      </button>
    </aside>
  );
};
