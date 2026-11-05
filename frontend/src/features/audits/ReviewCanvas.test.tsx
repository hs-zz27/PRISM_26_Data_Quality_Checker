import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { ReviewCanvas, type CanvasObject } from './ReviewCanvas';
import { colorFor, textColorFor } from './reviewWorkspace';
import { useCanvasStore } from '../../store/canvasStore';

describe('ReviewCanvas', () => {
  const sampleObjects: CanvasObject[] = [
    {
      object_id: 1,
      label: 'car',
      bbox: [10, 10, 50, 50],
      coordinate_system: 'zero_based_continuous',
      source_id: 1042,
      source_key: 'item_a',
    },
    {
      object_id: 2,
      label: 'pedestrian',
      bbox: [12, 12, 52, 52],
      coordinate_system: 'zero_based_continuous',
      source_id: 'src_2',
    },
  ];

  it('renders compact numbered badge markers with display numbers', () => {
    render(
      <ReviewCanvas
        imageSize={[200, 200]}
        objects={sampleObjects}
        findings={[]}
      />,
    );

    const badge1 = screen.getByText('#1', { selector: '.canvas-badge-text' });
    const badge2 = screen.getByText('#2', { selector: '.canvas-badge-text' });
    expect(badge1).toBeInTheDocument();
    expect(badge2).toBeInTheDocument();
  });

  it('uses matching color for object geometry and marker', () => {
    const { container } = render(
      <ReviewCanvas
        imageSize={[200, 200]}
        objects={sampleObjects}
        findings={[]}
      />,
    );

    const color1 = colorFor(1);
    const badgeRect = container.querySelector('g[class*="canvas-badge"] rect');
    expect(badgeRect).toHaveAttribute('fill', color1);

    const boxRect = container.querySelector('rect[stroke="' + color1 + '"]');
    expect(boxRect).toBeInTheDocument();
  });

  it('computes contrasting text colour for badge background', () => {
    expect(['#000', '#fff']).toContain(textColorFor(1));
    expect(['#000', '#fff']).toContain(textColorFor(2));
  });

  it('draws a leader line when overlapping badges are displaced', () => {
    // Both objects share the exact same tiny bounds, forcing badge displacement
    const overlapping: CanvasObject[] = [
      { object_id: 1, label: 'box1', bbox: [20, 20, 30, 30], coordinate_system: 'zero_based_continuous' },
      { object_id: 2, label: 'box2', bbox: [20, 20, 30, 30], coordinate_system: 'zero_based_continuous' },
      { object_id: 3, label: 'box3', bbox: [20, 20, 30, 30], coordinate_system: 'zero_based_continuous' },
    ];
    const { container } = render(
      <ReviewCanvas
        imageSize={[200, 200]}
        objects={overlapping}
        findings={[]}
      />,
    );

    // At least one displaced badge will have a leader line
    const lines = container.querySelectorAll('.canvas-badge line');
    expect(lines.length).toBeGreaterThan(0);
  });

  it('syncs selection when clicking geometry or badge', () => {
    const onSelect = vi.fn();
    useCanvasStore.setState({ selectedInstanceId: null });

    render(
      <ReviewCanvas
        imageSize={[200, 200]}
        objects={sampleObjects}
        findings={[]}
        onSelectObject={onSelect}
      />,
    );

    // Click badge #1
    const badge1 = screen.getByText('#1', { selector: '.canvas-badge-text' });
    fireEvent.click(badge1.closest('g')!);

    expect(onSelect).toHaveBeenCalledWith(1);
    expect(useCanvasStore.getState().selectedInstanceId).toBe(1);

    // Badge and box should now reflect selection state
    expect(badge1.closest('g')).toHaveClass('is-selected');
  });

  it('maintains non-scaling stroke on geometry and badges for zoom alignment', () => {
    const { container } = render(
      <ReviewCanvas
        imageSize={[200, 200]}
        objects={sampleObjects}
        findings={[]}
      />,
    );

    const rects = container.querySelectorAll('svg.canvas-overlay rect');
    rects.forEach((rect) => {
      expect(rect).toHaveAttribute('vector-effect', 'non-scaling-stroke');
    });
  });

  beforeEach(() => {
    useCanvasStore.setState({
      showBoxes: true,
      showPolygons: true,
      showLabels: true,
      showMask: true,
      showLegend: false,
      zoom: 1,
      pan: { x: 0, y: 0 },
      selectedInstanceId: null,
    });
  });

  it('renders solid border for supplied boxes and dashed border for derived boxes', () => {
    const mixedObjects: CanvasObject[] = [
      {
        object_id: 1,
        label: 'car',
        bbox: [10, 10, 50, 50],
        coordinate_system: 'zero_based_continuous',
        bbox_origin: 'supplied',
      },
      {
        object_id: 2,
        label: 'pedestrian',
        bbox: [60, 60, 90, 90],
        coordinate_system: 'zero_based_continuous',
        bbox_origin: 'derived_from_polygon',
      },
    ];

    const { container } = render(
      <ReviewCanvas
        imageSize={[200, 200]}
        objects={mixedObjects}
        findings={[]}
      />,
    );

    const rects = container.querySelectorAll('svg.canvas-overlay g[class*="box-"] rect');
    expect(rects.length).toBe(2);
    // Object 1: supplied -> solid line (strokeDasharray: 'none')
    expect(rects[0]).toHaveAttribute('stroke-dasharray', 'none');
    // Object 2: derived -> dashed line (strokeDasharray: '6 4')
    expect(rects[1]).toHaveAttribute('stroke-dasharray', '6 4');
  });

  it('explains all symbols and layers in plain words in the legend', () => {
    useCanvasStore.setState({ showLegend: true });
    render(
      <ReviewCanvas
        imageSize={[200, 200]}
        objects={sampleObjects}
        findings={[]}
        isCorrection={true}
      />,
    );

    expect(screen.getByText(/Solid line: supplied geometry/i)).toBeInTheDocument();
    expect(screen.getByText(/Dashed line: derived box/i)).toBeInTheDocument();
    expect(screen.getByText(/Display number matching object list entry/i)).toBeInTheDocument();
    expect(screen.getByText(/Bounding box is derived from polygon vertices/i)).toBeInTheDocument();
    expect(screen.getByText(/Diagnostic finding flagged or unsure for this object/i)).toBeInTheDocument();
    expect(screen.getByText(/Showing proposed correction \(preview, not saved\)/i)).toBeInTheDocument();
  });

  it('toggles visibility of boxes, polygons, labels, and mask via toolbar buttons', () => {
    useCanvasStore.setState({ showBoxes: true, showPolygons: true, showLabels: true, showMask: true });
    render(
      <ReviewCanvas
        imageSize={[200, 200]}
        objects={sampleObjects}
        findings={[]}
      />,
    );

    const toggleBoxesBtn = screen.getByRole('button', { name: 'Toggle boxes' });
    const togglePolygonsBtn = screen.getByRole('button', { name: 'Toggle polygons' });
    const toggleLabelsBtn = screen.getByRole('button', { name: 'Toggle labels' });
    const toggleMaskBtn = screen.getByRole('button', { name: 'Toggle mask' });

    expect(toggleBoxesBtn).toHaveClass('is-on');
    fireEvent.click(toggleBoxesBtn);
    expect(useCanvasStore.getState().showBoxes).toBe(false);

    fireEvent.click(togglePolygonsBtn);
    expect(useCanvasStore.getState().showPolygons).toBe(false);

    fireEvent.click(toggleLabelsBtn);
    expect(useCanvasStore.getState().showLabels).toBe(false);

    fireEvent.click(toggleMaskBtn);
    expect(useCanvasStore.getState().showMask).toBe(false);
  });

  it('supports overlap hit-testing where smallest object wins first and clicks cycle overlapping objects', () => {
    const onSelect = vi.fn();
    useCanvasStore.setState({ selectedInstanceId: null, zoom: 1, pan: { x: 0, y: 0 } });

    // Object 1: large [10, 10, 100, 100], area = 8100
    // Object 2: small [20, 20, 40, 40], area = 400
    const overlappingObjects: CanvasObject[] = [
      { object_id: 1, label: 'large', bbox: [10, 10, 100, 100], coordinate_system: 'zero_based_continuous' },
      { object_id: 2, label: 'small', bbox: [20, 20, 40, 40], coordinate_system: 'zero_based_continuous' },
    ];

    const { container } = render(
      <ReviewCanvas
        imageSize={[200, 200]}
        objects={overlappingObjects}
        findings={[]}
        onSelectObject={onSelect}
      />,
    );

    vi.spyOn(Element.prototype, 'getBoundingClientRect').mockReturnValue({
      left: 0, top: 0, right: 200, bottom: 200, width: 200, height: 200, x: 0, y: 0, toJSON: () => {},
    });

    const overlaySvg = container.querySelector('svg.canvas-overlay') as SVGElement;
    expect(overlaySvg).toBeInTheDocument();

    // First click at (30, 30) - inside both. Smallest object (#2) wins.
    fireEvent.click(overlaySvg, { clientX: 30, clientY: 30 });
    expect(onSelect).toHaveBeenLastCalledWith(2);
    expect(useCanvasStore.getState().selectedInstanceId).toBe(2);

    // Second click at (30, 30) - cycles to next overlapping object (#1).
    fireEvent.click(overlaySvg, { clientX: 30, clientY: 30 });
    expect(onSelect).toHaveBeenLastCalledWith(1);
    expect(useCanvasStore.getState().selectedInstanceId).toBe(1);

    // Third click at (30, 30) - cycles back to #2.
    fireEvent.click(overlaySvg, { clientX: 30, clientY: 30 });
    expect(onSelect).toHaveBeenLastCalledWith(2);
    expect(useCanvasStore.getState().selectedInstanceId).toBe(2);
  });

  it('zooms to fit the selected object when zoom button is clicked', () => {
    useCanvasStore.setState({ selectedInstanceId: 1, zoom: 1, pan: { x: 0, y: 0 } });

    const singleObject: CanvasObject[] = [
      { object_id: 1, label: 'target', bbox: [20, 20, 60, 60], coordinate_system: 'zero_based_continuous' },
    ];

    const { container } = render(
      <ReviewCanvas
        imageSize={[200, 200]}
        objects={singleObject}
        findings={[]}
      />,
    );

    const canvasRoot = container.querySelector('.review-canvas') as HTMLElement;
    vi.spyOn(canvasRoot, 'getBoundingClientRect').mockReturnValue({
      left: 0, top: 0, right: 400, bottom: 400, width: 400, height: 400, x: 0, y: 0, toJSON: () => {},
    });

    const zoomToBtn = screen.getByRole('button', { name: 'Zoom to selected object' });
    fireEvent.click(zoomToBtn);

    const { zoom, pan } = useCanvasStore.getState();
    expect(zoom).toBeGreaterThan(1);
    // The stage scales around its centre, so the object's centre (40,40)
    // must move by its offset from the image centre (100,100) times zoom.
    expect(pan.x).toBeCloseTo(60 * zoom);
    expect(pan.y).toBeCloseTo(60 * zoom);
  });

  it('selects an object from the keyboard without needing a pointer position', () => {
    const onSelect = vi.fn();
    useCanvasStore.setState({ selectedInstanceId: null });

    render(
      <ReviewCanvas
        imageSize={[200, 200]}
        objects={sampleObjects}
        findings={[]}
        onSelectObject={onSelect}
      />,
    );

    const object2 = screen.getByRole('button', { name: /^Object 2, pedestrian/ });
    fireEvent.keyDown(object2, { key: 'Enter' });

    expect(onSelect).toHaveBeenCalledWith(2);
    expect(useCanvasStore.getState().selectedInstanceId).toBe(2);
  });

  it('never draws a guessed location for an object without a box or polygon', () => {
    const { container } = render(
      <ReviewCanvas
        imageSize={[200, 200]}
        objects={[...sampleObjects, { object_id: 3, label: 'ghost', bbox: null, coordinate_system: 'zero_based_continuous', polygons: [] }]}
        findings={[]}
      />,
    );

    expect(screen.queryByRole('button', { name: /^Object 3, ghost/ })).toBeNull();
    expect(container.querySelector('.canvas-overlay circle')).toBeNull();
    expect(screen.queryByText('#3', { selector: '.canvas-badge-text' })).toBeNull();
    expect(screen.getByRole('status', { name: 'Objects not drawn on the image' })).toHaveTextContent('#3ghost: not drawn, the annotation has no box or polygon');
  });

  it('colours box outlines by object, not by finding outcome', () => {
    const { container } = render(
      <ReviewCanvas
        imageSize={[200, 200]}
        objects={sampleObjects}
        findings={[]}
      />,
    );

    const boxes = container.querySelectorAll<SVGRectElement>('g[role="button"] rect');
    expect(boxes[0]?.style.stroke).toBe(colorFor(1));
    expect(boxes[1]?.style.stroke).toBe(colorFor(2));
  });
});

