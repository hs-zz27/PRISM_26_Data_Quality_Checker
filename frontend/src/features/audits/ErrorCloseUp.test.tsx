import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { ErrorCloseUp } from './ErrorCloseUp';
import { closeUpRegions } from './closeUpRegions';
import type { CanvasObject } from './ReviewCanvas';
import { makeFinding } from '../../test/fixtures';

const IMAGE: [number, number] = [640, 480];
const outline = [[280, 180], [380, 180], [380, 460], [280, 460]];
const objects: CanvasObject[] = [
  { object_id: 1, label: 'person', coordinate_system: 'zero_based_continuous', source_key: '/shapes/0',
    polygons: [outline], bbox: [280, 180, 380, 460], bbox_origin: 'derived_from_polygon' },
  { object_id: 2, label: 'person', coordinate_system: 'zero_based_continuous', source_key: '/shapes/1',
    polygons: [[[10, 10], [60, 10], [60, 200], [10, 200]]], bbox: [10, 10, 60, 200], bbox_origin: 'derived_from_polygon' },
];
const measurements = { result: { problems: ['polygon: head/top of the person is outside the annotation (900 px area)'], geometries: [
  { geometry: 'polygon', coordinate_system: 'zero_based_continuous', regions: [
    { box: [300, 120, 360, 180], kind: 'missing', side: 'top', area_px: 900 },
    { box: [380, 300, 420, 420], kind: 'extra', side: 'right', area_px: 400 },
    { box: [600, 440, 640, 480], kind: 'mismatch', side: 'bottom', area_px: 100 }] }] } };
const finding = makeFinding({
  check_name: 'person_coverage', outcome: 'fail', object_id: 1, rule_id: 'RULE-2',
  description: 'Each person annotation must cover the whole person.',
  reason: 'polygon: head/top of the person is outside the annotation (900 px area)',
  evidence: { measurements } as never,
});

function renderCloseUp(overrides: Partial<Parameters<typeof ErrorCloseUp>[0]> = {}) {
  const onClose = vi.fn();
  const regions = closeUpRegions(finding, IMAGE);
  const view = render(<ErrorCloseUp finding={finding} regions={regions} imageUrl="/original.jpg" imageSize={IMAGE}
    objects={objects} annotationFormat="labelme" onClose={onClose} {...overrides} />);
  return { ...view, onClose, regions };
}

afterEach(() => vi.restoreAllMocks());

describe('ErrorCloseUp', () => {
  it('shows a crop of the original photo around the problem, with the outline and the finding sentence', () => {
    const { container } = renderCloseUp();
    const panel = screen.getByRole('region', { name: 'Error close-up' });

    expect(panel).toHaveTextContent('Polygon: head/top of the person is outside the annotation (900 px area)');
    expect(panel).toHaveTextContent('Missing part: head or top of the person');
    expect(panel).toHaveTextContent('Outline, about 900 px in area');
    expect(panel).toHaveTextContent('Region 1 of 3');

    // The crop is the original photo seen through a window: padded region, bounded size.
    const crop = screen.getByRole('img', { name: /^Close-up of the photo\. Missing part: head or top of the person/ });
    expect(crop).toHaveAttribute('viewBox', '204 24 252 252');
    expect(crop).toHaveAttribute('width', '420');
    expect(crop).toHaveAttribute('height', '420');
    const photo = crop.querySelector('image')!;
    expect(photo).toHaveAttribute('href', '/original.jpg');
    expect([photo.getAttribute('x'), photo.getAttribute('y'), photo.getAttribute('width'), photo.getAttribute('height')])
      .toEqual(['0', '0', '640', '480']);
    expect(crop.querySelectorAll('image')).toHaveLength(1);
    expect(container.querySelector('canvas')).toBeNull();

    // The problem area: its own pattern, line style and word, not only a colour.
    const area = crop.querySelector('rect.close-up-region')!;
    expect([area.getAttribute('x'), area.getAttribute('y'), area.getAttribute('width'), area.getAttribute('height')])
      .toEqual(['300', '120', '60', '60']);
    expect(area).toHaveAttribute('data-kind', 'missing');
    expect(area.getAttribute('fill')).toMatch(/^url\(#.+diagonal\)$/);
    expect(within(crop as unknown as HTMLElement).getByText('MISSING', { selector: '.close-up-word' })).toBeInTheDocument();

    // The annotation the finding is about, with the file's own points. Other annotations are left out.
    const outlines = [...crop.querySelectorAll('polygon.close-up-outline')].map((node) => node.getAttribute('points'));
    expect(outlines).toEqual(['280,180 380,180 380,460 280,460']);
    expect(panel).toHaveTextContent('The photo and the annotation file are not changed');
    expect(panel).toHaveTextContent('Small differences are accepted. Only a noticeable one is marked.');
  });

  it('steps through the regions with buttons and arrow keys', () => {
    renderCloseUp();
    const panel = screen.getByRole('region', { name: 'Error close-up' });
    const previous = screen.getByRole('button', { name: 'Previous region' });
    const next = screen.getByRole('button', { name: 'Next region' });
    expect(previous).toBeDisabled();

    fireEvent.click(next);
    expect(panel).toHaveTextContent('Region 2 of 3');
    expect(panel).toHaveTextContent('Extra background: on the right');
    expect(panel.querySelector('rect.close-up-region')).toHaveAttribute('data-kind', 'extra');
    expect(panel.querySelector('rect.close-up-region')!.getAttribute('fill')).toMatch(/dots\)$/);
    expect(within(panel).getByText('EXTRA', { selector: '.close-up-word' })).toBeInTheDocument();

    fireEvent.keyDown(panel, { key: 'ArrowRight' });
    expect(panel).toHaveTextContent('Region 3 of 3');
    expect(panel).toHaveTextContent('Wrong shape: feet or bottom of the person');
    expect(next).toBeDisabled();
    fireEvent.keyDown(panel, { key: 'ArrowRight' });
    expect(panel).toHaveTextContent('Region 3 of 3');

    fireEvent.keyDown(panel, { key: 'ArrowLeft' });
    fireEvent.click(previous);
    expect(panel).toHaveTextContent('Region 1 of 3');
  });

  it('shows a region at the edge of the photo as being at the edge', () => {
    renderCloseUp();
    const panel = screen.getByRole('region', { name: 'Error close-up' });
    expect(panel.querySelectorAll('line.close-up-edge')).toHaveLength(0);
    fireEvent.keyDown(panel, { key: 'End' });

    expect(panel).toHaveTextContent('Region 3 of 3');
    expect(panel).toHaveTextContent('The problem area touches the right and bottom edges of the photo.');
    expect([...panel.querySelectorAll('line.close-up-edge')].map((line) => line.getAttribute('data-side'))).toEqual(['right', 'bottom']);
    // The window stops at the photo's border; nothing outside the photo is invented.
    const crop = panel.querySelector('svg.close-up-crop')!;
    const [x, y, width, height] = crop.getAttribute('viewBox')!.split(' ').map(Number);
    expect(x! + width!).toBe(640);
    expect(y! + height!).toBe(480);
    fireEvent.keyDown(panel, { key: 'Home' });
    expect(panel).toHaveTextContent('Region 1 of 3');
  });

  it('closes from the button and from Escape, and takes the keyboard focus when it opens', () => {
    const { onClose } = renderCloseUp();
    const panel = screen.getByRole('region', { name: 'Error close-up' });
    expect(document.activeElement).toBe(panel);

    fireEvent.click(screen.getByRole('button', { name: 'Close the error close-up' }));
    expect(onClose).toHaveBeenCalledTimes(1);
    fireEvent.keyDown(panel, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(2);
  });

  it('keeps its keys to itself, so stepping regions never moves to another image', () => {
    const pageKeys = vi.fn();
    window.addEventListener('keydown', pageKeys);
    try {
      renderCloseUp();
      const panel = screen.getByRole('region', { name: 'Error close-up' });
      for (const key of ['ArrowRight', 'ArrowLeft', 'Escape', 'Home', 'End']) fireEvent.keyDown(panel, { key });
      expect(pageKeys).not.toHaveBeenCalled();
      // Keys it does not use still reach the page (review shortcuts keep working).
      fireEvent.keyDown(panel, { key: 'a' });
      expect(pageKeys).toHaveBeenCalledTimes(1);
    } finally {
      window.removeEventListener('keydown', pageKeys);
    }
  });

  it('explains every kind of marking in a legend', () => {
    renderCloseUp();
    const legend = screen.getByRole('list', { name: 'What the markings mean' });
    const rows = within(legend).getAllByRole('listitem').map((row) => row.textContent);
    expect(rows).toEqual([
      'MISSING A visible part of the person is outside the annotation',
      'EXTRA The annotation takes in background that is not the person',
      'WRONG SHAPE The outline does not follow the person’s visible boundary',
      'NO ANNOTATION A visible person has no annotation',
      'Annotation outline, as the file has it',
    ]);
    expect(legend.querySelectorAll('svg rect[fill^="url(#"]')).toHaveLength(4);
  });

  it('has no stepping buttons for a single region, and draws every outline for a person with no annotation', () => {
    const lone = makeFinding({ check_name: 'unannotated_people', object_id: null, reason: '1 person has no annotation',
      evidence: { measurements: { result: { coordinate_system: 'zero_based_continuous',
        unannotated: [{ box: [450, 100, 560, 400], height_px: 300, score: 0.99 }] } } } as never });
    renderCloseUp({ finding: lone, regions: closeUpRegions(lone, IMAGE) });
    const panel = screen.getByRole('region', { name: 'Error close-up' });

    expect(panel).toHaveTextContent('Person with no annotation');
    expect(panel).toHaveTextContent('About 300 px tall');
    expect(panel).not.toHaveTextContent('Region 1 of');
    expect(screen.queryByRole('button', { name: 'Next region' })).toBeNull();
    expect(panel.querySelector('rect.close-up-region')).toHaveAttribute('data-kind', 'unannotated');
    expect(within(panel).getByText('NO ANNOTATION', { selector: '.close-up-word' })).toBeInTheDocument();
    expect(panel.querySelectorAll('polygon.close-up-outline')).toHaveLength(2);
  });

  it('draws a Penn-Fudan box as the outline, in image pixels', () => {
    const penn: CanvasObject[] = [{ object_id: 1, label: 'person', bbox: [281, 181, 380, 460] }];
    const box = makeFinding({ check_name: 'person_coverage', object_id: 1, reason: 'box: head outside',
      evidence: { measurements: { result: { geometries: [{ geometry: 'box', coordinate_system: 'one_based_inclusive',
        regions: [{ box: [281, 121, 380, 180], kind: 'missing', side: 'top', pixels: 60, edge: 121 }] }] } } } as never });
    renderCloseUp({ finding: box, regions: closeUpRegions(box, IMAGE), objects: penn, annotationFormat: 'penn_fudan' });
    const panel = screen.getByRole('region', { name: 'Error close-up' });

    const drawn = panel.querySelector('rect.close-up-outline')!;
    expect([drawn.getAttribute('x'), drawn.getAttribute('y'), drawn.getAttribute('width'), drawn.getAttribute('height')])
      .toEqual(['280', '180', '100', '280']);
    const area = panel.querySelector('rect.close-up-region')!;
    expect([area.getAttribute('x'), area.getAttribute('y'), area.getAttribute('width'), area.getAttribute('height')])
      .toEqual(['280', '120', '100', '60']);
    expect(panel).toHaveTextContent('Box, off by 60 px');
  });

  it('outlines the supplied box for a problem measured on the box, even when the object also has a polygon', () => {
    const both: CanvasObject[] = [{ ...objects[0]!, bbox: [270, 170, 390, 470], bbox_origin: 'supplied' }];
    const box = makeFinding({ check_name: 'person_coverage', object_id: 1, reason: 'box: head outside',
      evidence: { measurements: { result: { geometries: [{ geometry: 'box', coordinate_system: 'zero_based_continuous',
        regions: [{ box: [270, 120, 390, 170], kind: 'missing', side: 'top', pixels: 50, edge: 120 }] }] } } } as never });
    renderCloseUp({ finding: box, regions: closeUpRegions(box, IMAGE), objects: both, annotationFormat: 'coco' });
    const panel = screen.getByRole('region', { name: 'Error close-up' });
    expect(panel.querySelectorAll('polygon.close-up-outline')).toHaveLength(0);
    expect(panel.querySelector('rect.close-up-outline')).toHaveAttribute('x', '270');
    // A box worked out from the polygon is never drawn: the polygon stays the outline.
    renderCloseUp({ finding: box, regions: closeUpRegions(box, IMAGE), objects, annotationFormat: 'labelme' });
    const second = screen.getAllByRole('region', { name: 'Error close-up' })[1]!;
    expect(second.querySelectorAll('rect.close-up-outline')).toHaveLength(0);
    expect(second.querySelectorAll('polygon.close-up-outline')).toHaveLength(1);
  });

  it('says so when the photo cannot be shown, and renders nothing without regions', () => {
    const { container, unmount } = renderCloseUp({ imageUrl: null });
    expect(screen.getByRole('region', { name: 'Error close-up' })).toHaveTextContent('The photo is not available, so the close-up cannot be drawn.');
    expect(container.querySelector('svg.close-up-crop')).toBeNull();
    unmount();
    const empty = renderCloseUp({ regions: [] });
    expect(empty.container).toBeEmptyDOMElement();
  });
});
