import { useState } from 'react';
import FileUpload from './components/FileUpload';
import PageViewer from './components/PageViewer';
import { BlockData } from './components/EditableBlock';
import Toolbar from './components/Toolbar';
import SplitDialog, { SplitPayload } from './components/SplitDialog';
import { requestSplit } from './lib/requestSplit';
import { API_BASE } from './lib/config';
import { validatePageCount } from './lib/validateUpload';

interface PageData {
    index: number;
    width: number;
    height: number;
    blocks: BlockData[];
}

function App() {
    const [fileId, setFileId] = useState<string | null>(null);
    const [pages, setPages] = useState<PageData[]>([]);
    const [isUploading, setIsUploading] = useState(false);
    const [isSaving, setIsSaving] = useState(false);
    const [error, setError] = useState<string | null>(null);

    // Editor State
    const [selectedBlockId, setSelectedBlockId] = useState<string | null>(null);
    const [selectedPageIndex, setSelectedPageIndex] = useState<number | null>(null);
    const [splitOpen, setSplitOpen] = useState(false);
    const [isSplitting, setIsSplitting] = useState(false);


    // Derived selected block
    const getSelectedBlock = () => {
        if (selectedPageIndex === null || !selectedBlockId) return null;
        return pages[selectedPageIndex]?.blocks.find(b => b.id === selectedBlockId);
    };

    const selectedBlock = getSelectedBlock();

    const handleUpload = async (file: File) => {
        setIsUploading(true);
        setError(null);

        const formData = new FormData();
        formData.append('file', file);

        try {
            const response = await fetch(`${API_BASE}/upload`, {
                method: 'POST',
                body: formData,
            });

            if (!response.ok) {
                throw new Error('Upload failed');
            }

            const data = await response.json();

            const pageCheck = validatePageCount(data.pages?.length ?? 0);
            if (!pageCheck.ok) {
                setError(pageCheck.error);
                return;
            }

            setFileId(data.file_id);
            setPages(data.pages);
        } catch (err) {
            console.error(err);
            setError("Failed to process PDF. Please check backend connection.");
        } finally {
            setIsUploading(false);
        }
    };

    const handleBlockChange = (pageIndex: number, blockId: string, updates: Partial<BlockData>) => {
        setPages(prevPages => {
            const newPages = [...prevPages];
            const page = { ...newPages[pageIndex] };
            page.blocks = page.blocks.map(b =>
                b.id === blockId ? { ...b, ...updates } : b
            );
            newPages[pageIndex] = page;
            return newPages;
        });
    };



    const handleDeleteBlock = () => {
        if (selectedPageIndex === null || !selectedBlockId) return;
        setPages(prevPages => {
            const newPages = [...prevPages];
            const page = { ...newPages[selectedPageIndex] };
            page.blocks = page.blocks.filter(b => b.id !== selectedBlockId);
            newPages[selectedPageIndex] = page;
            return newPages;
        });
        setSelectedBlockId(null);
    };

    const handleSelectBlock = (pageIndex: number, blockId: string | null) => {
        setSelectedPageIndex(pageIndex);
        setSelectedBlockId(blockId);
    };

    const updateSelectedBlockStyle = (styleUpdates: any) => {
        if (selectedPageIndex === null || !selectedBlockId) return;

        const currentBlock = getSelectedBlock();
        const newStyle = { ...currentBlock?.style, ...styleUpdates };

        handleBlockChange(selectedPageIndex, selectedBlockId, { style: newStyle });
    };



    const handleSave = async () => {
        if (!fileId) return;
        setIsSaving(true);

        const modifications = [];
        for (const page of pages) {
            for (const block of page.blocks) {
                modifications.push({
                    page: page.index,
                    id: block.id,
                    content: block.content || "",
                    bbox: block.bbox,
                    style: block.style || {},
                    script: block.script || 'latin',  // Include script for font selection
                    type: block.type,
                    image_data: block.image_data,
                    mask_background: block.mask_background !== false // default true
                });
            }
        }

        try {
            const response = await fetch(`${API_BASE}/save`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    file_id: fileId,
                    modifications: modifications
                })
            });

            if (!response.ok) throw new Error("Save failed");

            // Trigger download
            const blob = await response.blob();
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `edited_${fileId}.pdf`;
            document.body.appendChild(a);
            a.click();
            window.URL.revokeObjectURL(url);
            document.body.removeChild(a);

        } catch (err) {
            console.error(err);
            alert("Failed to save PDF.");
        } finally {
            setIsSaving(false);
        }
    };

    const handleReset = () => {
        setFileId(null);
        setPages([]);
        setError(null);
        setSelectedBlockId(null);
    };

    const handleSplit = async (payload: SplitPayload) => {
        setIsSplitting(true);
        try {
            const blob = await requestSplit(API_BASE, payload);
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `split_${payload.file_id}.pdf`;
            document.body.appendChild(a);
            a.click();
            window.URL.revokeObjectURL(url);
            document.body.removeChild(a);
            setSplitOpen(false);
        } catch (err) {
            console.error(err);
            alert(err instanceof Error ? err.message : 'Split failed');
        } finally {
            setIsSplitting(false);
        }
    };

    return (
        <div className="flex h-screen bg-[#f8fafc] overflow-hidden font-sans text-slate-800">
            {/* Main Content Area */}
            <div className="flex-1 flex flex-col h-full overflow-hidden relative">

                {/* Header */}
                <header className="bg-white/80 backdrop-blur-md border-b px-8 py-4 flex justify-between items-center z-10 sticky top-0">
                    <div className="flex items-center gap-3">
                        <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-blue-600 to-indigo-600 flex items-center justify-center text-white font-bold shadow-lg shadow-blue-200">
                            P
                        </div>
                        <h1 className="text-xl font-bold text-slate-800 tracking-tight">
                            PDF <span className="text-blue-600">Editor</span>
                        </h1>
                    </div>

                    {fileId && (
                        <div className="flex items-center gap-3">
                            <button
                                onClick={handleReset}
                                className="text-slate-500 hover:text-slate-800 font-medium px-4 py-2 text-sm transition-colors"
                            >
                                Upload New
                            </button>
                            <button
                                onClick={() => setSplitOpen(true)}
                                disabled={isSplitting}
                                className="bg-white text-slate-700 border border-slate-300 px-4 py-2 rounded-lg font-medium hover:bg-slate-50 transition-all text-sm disabled:opacity-50"
                            >
                                {isSplitting ? 'Splitting...' : 'Split in Half'}
                            </button>
                            <button
                                onClick={handleSave}
                                disabled={isSaving}
                                className="bg-slate-900 text-white px-5 py-2 rounded-lg font-medium hover:bg-slate-800 transition-all shadow-lg shadow-slate-200 disabled:opacity-50 disabled:shadow-none text-sm flex items-center gap-2"
                            >
                                {isSaving ? (
                                    <>
                                        <svg className="animate-spin h-4 w-4 text-white" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                                            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                                            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                                        </svg>
                                        Saving...
                                    </>
                                ) : (
                                    <>
                                        <span>Save PDF</span>
                                        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" /></svg>
                                    </>
                                )}
                            </button>
                        </div>
                    )}
                </header>

                {/* Scrollable Canvas Area */}
                <main className="flex-1 overflow-y-auto p-8 relative">
                    {!fileId ? (
                        <div className="flex flex-col items-center justify-center min-h-[60vh] animate-fade-in-up">
                            <div className="text-center max-w-2xl mb-12">
                                <h2 className="text-5xl font-black text-slate-900 mb-6 tracking-tight leading-tight">
                                    Edit PDFs <br />
                                    <span className="text-transparent bg-clip-text bg-gradient-to-r from-blue-600 to-indigo-600">Like Magic.</span>
                                </h2>
                                <p className="text-xl text-slate-500 font-light leading-relaxed">
                                    Upload your PDF to start editing text, images, and layout instantly directly in your browser.
                                </p>
                            </div>
                            <FileUpload onUpload={handleUpload} isUploading={isUploading} />
                            {error && (
                                <div className="mt-8 p-4 bg-red-50 text-red-600 rounded-xl border border-red-200 shadow-sm animate-shake">
                                    {error}
                                </div>
                            )}
                        </div>
                    ) : (
                        <div className="flex flex-col items-center gap-8 pb-32">
                            {pages.map(page => (
                                <PageViewer
                                    key={page.index}
                                    page={page}
                                    fileId={fileId || ''}
                                    onBlockChange={handleBlockChange}
                                    selectedBlockId={selectedBlockId}
                                    onSelectBlock={(id) => handleSelectBlock(page.index, id)}
                                />
                            ))}
                        </div>
                    )}
                </main>
            </div>

            {/* Split Dialog */}
            {fileId && (
                <SplitDialog
                    open={splitOpen}
                    fileId={fileId}
                    onClose={() => setSplitOpen(false)}
                    onSubmit={handleSplit}
                />
            )}

            {/* Sidebar Toolbar (Only visible when file is loaded) */}
            {fileId && (
                <Toolbar
                    selectedBlockId={selectedBlockId}
                    fontFamily={selectedBlock?.style?.fontFamily || 'Arial'}
                    fontSize={selectedBlock?.style?.fontSize || 12}
                    onFontFamilyChange={(f) => updateSelectedBlockStyle({ fontFamily: f })}
                    onFontSizeChange={(s) => updateSelectedBlockStyle({ fontSize: s })}
                    onDeleteBlock={handleDeleteBlock}
                />
            )}
        </div>
    );
}

export default App;
