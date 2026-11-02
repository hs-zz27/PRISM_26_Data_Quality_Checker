import { useState } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { PolygonEditor } from './PolygonEditor';
import { polygonObjects } from './polygonFixtures';

function Harness({ disabled = false }: { disabled?: boolean }) {
  const [objects, setObjects] = useState(polygonObjects);
  return <><PolygonEditor objects={objects} savedObjects={polygonObjects()} imageSize={[100, 80]}
    imageUrl="/synthetic.png" sampleId="sample" disabled={disabled} onChange={setObjects} />
    <output data-testid="geometry">{JSON.stringify(objects)}</output></>;
}
function geometry() { return JSON.parse(screen.getByTestId('geometry').textContent!); }
function selectFirst() { fireEvent.change(screen.getByLabelText('Polygon object'), { target: { value: '/shapes/0' } }); }

describe('polygon editing controls', () => {
  beforeEach(() => {
    // jsdom has no pointer capture/layout. Events still go through React handlers.
    class TestPointerEvent extends MouseEvent {
      pointerId: number;
      constructor(type: string, options: PointerEventInit = {}) { super(type, options); this.pointerId = options.pointerId ?? 1; }
    }
    vi.stubGlobal('PointerEvent', TestPointerEvent);
    Object.defineProperty(SVGElement.prototype, 'setPointerCapture', { configurable: true, value: vi.fn() });
    Object.defineProperty(SVGElement.prototype, 'hasPointerCapture', { configurable: true, value: () => false });
  });
  afterEach(() => {
    cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks();
    Reflect.deleteProperty(SVGElement.prototype, 'setPointerCapture');
    Reflect.deleteProperty(SVGElement.prototype, 'hasPointerCapture');
  });

  it('drags at zoomed/scrolled coordinates and undoes the entire gesture once', () => {
    render(<Harness />); selectFirst();
    fireEvent.click(screen.getByRole('button', { name: 'Zoom in polygon editor' }));
    const canvas = screen.getByLabelText('Editable polygon outlines');
    vi.spyOn(canvas, 'getBoundingClientRect').mockReturnValue({ left: -50, top: 20, width: 200, height: 160 } as DOMRect);
    fireEvent.pointerDown(screen.getByRole('button', { name: 'Object 1 polygon 1 point 1' }), { pointerId: 7, button: 0 });
    fireEvent.pointerMove(canvas, { pointerId: 7, clientX: -30, clientY: 40 });
    fireEvent.pointerMove(canvas, { pointerId: 7, clientX: -39.5, clientY: 30 });
    fireEvent.pointerUp(canvas, { pointerId: 7 });
    expect(geometry()[0].polygons[0][0]).toEqual([5.25, 5]);
    expect(geometry()[1]).toEqual(polygonObjects()[1]);
    fireEvent.click(screen.getByRole('button', { name: 'Undo' }));
    expect(geometry()).toEqual(polygonObjects());
    expect(screen.getByRole('button', { name: 'Undo' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Redo' }));
    expect(geometry()[0].polygons[0][0]).toEqual([5.25, 5]);
  });

  it('cancels an interrupted drag without leaving a partial edit', () => {
    render(<Harness />); selectFirst();
    const canvas = screen.getByLabelText('Editable polygon outlines');
    vi.spyOn(canvas, 'getBoundingClientRect').mockReturnValue({ left: 0, top: 0, width: 100, height: 80 } as DOMRect);
    fireEvent.pointerDown(screen.getByRole('button', { name: 'Object 1 polygon 1 point 1' }), { button: 0, pointerId: 1 });
    fireEvent.pointerMove(canvas, { pointerId: 1, clientX: 5, clientY: 5 });
    fireEvent.pointerCancel(canvas, { pointerId: 1 });
    expect(geometry()).toEqual(polygonObjects());
    expect(screen.getByRole('button', { name: 'Undo' })).toBeDisabled();
  });

  it('supports keyboard adjustments, insert/remove and numeric precision', () => {
    render(<Harness />); selectFirst();
    fireEvent.keyDown(screen.getByRole('button', { name: 'Object 1 polygon 1 point 1' }), { key: 'ArrowLeft' });
    expect(geometry()[0].polygons[0][0]).toEqual([9.25, 10]);
    fireEvent.change(screen.getByLabelText('Selected point X'), { target: { value: '8.125' } });
    expect(geometry()[0].polygons[0][0]).toEqual([8.125, 10]);
    fireEvent.click(screen.getByRole('button', { name: 'Add point after selected' }));
    expect(geometry()[0].polygons[0]).toHaveLength(5);
    expect(geometry()[0].polygons[0][1]).toEqual([24.0625, 10]);
    fireEvent.click(screen.getByRole('button', { name: 'Remove selected point' }));
    expect(geometry()[0].polygons[0]).toHaveLength(4);
    fireEvent.click(screen.getByRole('button', { name: 'Remove selected point' }));
    expect(screen.getByRole('button', { name: 'Remove selected point' })).toBeDisabled();
  });

  it('keeps the saved comparison outline independent of edits', () => {
    render(<Harness />); selectFirst();
    fireEvent.change(screen.getByLabelText('Selected point X'), { target: { value: '4.5' } });
    fireEvent.click(screen.getByLabelText('Show saved outline'));
    const saved = screen.getByLabelText('Editable polygon outlines').querySelector('.polygon-saved');
    expect(saved).toHaveAttribute('points', '10.25,10 40,10 40,60 10.25,60');
    expect(geometry()[0].polygons[0][0][0]).toBe(4.5);
  });

  it('disables editing after an image load error or while saving', () => {
    const view = render(<Harness />); selectFirst();
    fireEvent.error(screen.getByRole('img', { name: 'sample' }));
    expect(screen.getByLabelText('Selected point X')).toBeDisabled();
    expect(screen.getByRole('alert')).toHaveTextContent('could not be loaded');
    view.unmount();
    render(<Harness disabled />);
    expect(screen.getByLabelText('Polygon object')).toBeDisabled();
    expect(geometry()).toEqual(polygonObjects());
  });
});
