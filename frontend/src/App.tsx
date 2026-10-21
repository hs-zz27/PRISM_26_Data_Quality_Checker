import React from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { QueryClientProvider } from '@tanstack/react-query';
import { queryClient } from './api/queryClient';
import { AppLayout } from './components/layout/AppLayout';

import { RulebooksPage } from './features/rulebooks/RulebooksPage';
import { RulebookEditorPage } from './features/rulebooks/RulebookEditorPage';
import { DatasetsPage } from './features/datasets/DatasetsPage';
import { DatasetDetailPage } from './features/datasets/DatasetDetailPage';
import { AuditsPage } from './features/audits/AuditsPage';
import { AuditReviewWorkspace } from './features/audits/AuditReviewWorkspace';
import { CorrectionsPage } from './features/corrections/CorrectionsPage';
import { ExportsPage } from './features/exports/ExportsPage';
import { FeedbackPage } from './features/feedback/FeedbackPage';
import { StyleguidePage } from './features/dev/StyleguidePage';

export const App: React.FC = () => {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<AppLayout />}>
            <Route index element={<Navigate to="/audits" replace />} />
            <Route path="rulebooks" element={<RulebooksPage />} />
            <Route path="rulebooks/:rulebookId" element={<RulebookEditorPage />} />
            <Route path="datasets" element={<DatasetsPage />} />
            <Route path="datasets/:datasetId" element={<DatasetDetailPage />} />
            <Route path="audits" element={<AuditsPage />} />
            <Route path="audits/:auditId/review" element={<AuditReviewWorkspace />} />
            <Route path="corrections" element={<CorrectionsPage />} />
            <Route path="exports" element={<ExportsPage />} />
            <Route path="feedback" element={<FeedbackPage />} />
            {import.meta.env.DEV && <Route path="dev/styleguide" element={<StyleguidePage />} />}
            <Route path="*" element={<Navigate to="/audits" replace />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  );
};
