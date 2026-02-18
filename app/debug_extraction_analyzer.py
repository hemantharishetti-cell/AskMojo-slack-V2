"""
Debug analyzer for PDF extraction and chunking results.

Saves extraction and chunking outputs to JSON files for inspection and improvement analysis.
"""

import json
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


class ExtractionAnalyzer:
    """Analyze and export extraction and chunking results for debugging."""
    
    @staticmethod
    def save_extraction_analysis(
        document_id: int,
        document_title: str,
        extracted_json: Dict[str, Any],
        chunks: List[Dict[str, Any]],
        export_dir: Optional[str] = None
    ) -> str:
        """
        Save extraction and chunking results to JSON files for analysis.
        
        Args:
            document_id: Document ID
            document_title: Document title
            extracted_json: Raw Adobe extraction JSON
            chunks: Final chunks created from extraction
            export_dir: Directory to save files (default: app/extraction_analysis/)
            
        Returns:
            Path to the analysis report
        """
        if export_dir is None:
            export_dir = Path(__file__).parent / "extraction_analysis"
        else:
            export_dir = Path(export_dir)
        
        export_dir.mkdir(exist_ok=True)
        
        # Create filename with timestamp
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        base_filename = f"doc_{document_id}_{timestamp}"
        
        try:
            # Save raw extraction
            extraction_file = export_dir / f"{base_filename}_extraction.json"
            with open(extraction_file, "w") as f:
                json.dump(extracted_json, f, indent=2)
            logger.info(f"[ANALYZER] Saved extraction data to: {extraction_file}")
            
            # Save chunks
            chunks_file = export_dir / f"{base_filename}_chunks.json"
            with open(chunks_file, "w") as f:
                json.dump(chunks, f, indent=2)
            logger.info(f"[ANALYZER] Saved chunks to: {chunks_file}")
            
            # Create analysis report
            report = ExtractionAnalyzer._generate_report(
                document_id,
                document_title,
                extracted_json,
                chunks
            )
            
            report_file = export_dir / f"{base_filename}_report.json"
            with open(report_file, "w") as f:
                json.dump(report, f, indent=2)
            logger.info(f"[ANALYZER] Saved analysis report to: {report_file}")
            
            return str(report_file)
            
        except Exception as e:
            logger.error(f"[ANALYZER] Error saving extraction analysis: {str(e)}")
            return ""
    
    @staticmethod
    def _generate_report(
        document_id: int,
        document_title: str,
        extracted_json: Dict[str, Any],
        chunks: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Generate analysis report with statistics and samples.
        
        Args:
            document_id: Document ID
            document_title: Document title
            extracted_json: Raw extraction
            chunks: Final chunks
            
        Returns:
            Report dictionary
        """
        elements = extracted_json.get("elements", [])
        
        # Element type distribution
        element_types = {}
        for elem in elements:
            path = elem.get("Path", "")
            if "/" in path:
                elem_type = path.split("/")[-1]
                element_types[elem_type] = element_types.get(elem_type, 0) + 1
        
        # Chunk statistics
        total_chars = sum(c.get("char_count", 0) for c in chunks)
        total_words = sum(c.get("word_count", 0) for c in chunks)
        avg_chars = total_chars / len(chunks) if chunks else 0
        
        # Chunk type distribution
        chunk_types = {}
        for chunk in chunks:
            types = chunk.get("element_types", [])
            for t in types:
                chunk_types[t] = chunk_types.get(t, 0) + 1
        
        # Element samples
        element_samples = []
        for elem in elements[:5]:
            element_samples.append({
                "path": elem.get("Path"),
                "text": elem.get("Text", "")[:100],
                "page": elem.get("Page")
            })
        
        # Chunk samples
        chunk_samples = []
        for chunk in chunks[:5]:
            chunk_samples.append({
                "chunk_index": chunk.get("chunk_index"),
                "section": chunk.get("section"),
                "page": chunk.get("page_number"),
                "text": chunk.get("text", "")[:200],
                "char_count": chunk.get("char_count"),
                "word_count": chunk.get("word_count")
            })
        
        return {
            "document": {
                "id": document_id,
                "title": document_title
            },
            "extraction": {
                "total_elements": len(elements),
                "element_types_distribution": element_types,
                "element_samples": element_samples
            },
            "chunking": {
                "total_chunks": len(chunks),
                "total_characters": total_chars,
                "total_words": total_words,
                "average_chunk_size": round(avg_chars, 0),
                "chunk_type_distribution": chunk_types,
                "chunk_samples": chunk_samples
            },
            "quality_metrics": {
                "utilization_rate": round((total_chars / (len(elements) * 100)) * 100, 1) if elements else 0,
                "smallest_chunk": min((c.get("char_count", 0) for c in chunks), default=0),
                "largest_chunk": max((c.get("char_count", 0) for c in chunks), default=0),
                "chunks_under_min_size": len([c for c in chunks if c.get("char_count", 0) < 100]),
                "chunks_over_max_size": len([c for c in chunks if c.get("char_count", 0) > 1000])
            },
            "recommendations": ExtractionAnalyzer._generate_recommendations(
                len(elements), len(chunks), total_chars, chunk_types
            )
        }
    
    @staticmethod
    def _generate_recommendations(
        element_count: int,
        chunk_count: int,
        total_chars: int,
        chunk_types: Dict[str, int]
    ) -> List[str]:
        """Generate recommendations based on extraction results."""
        recommendations = []
        
        if chunk_count == 0:
            recommendations.append("⚠️  No chunks created - check element parsing logic")
        elif chunk_count < element_count / 10:
            recommendations.append("⚠️  Low chunk-to-element ratio - may be over-aggregating content")
        
        if chunk_count > 0:
            avg_chunk_size = total_chars / chunk_count
            if avg_chunk_size < 100:
                recommendations.append("⚠️  Average chunk size is very small - consider larger chunks")
            elif avg_chunk_size > 1500:
                recommendations.append("⚠️  Average chunk size is very large - consider splitting chunks")
        
        # Check element type coverage
        expected_types = {"H1", "H2", "H3", "P"}
        missing_types = expected_types - set(chunk_types.keys())
        if missing_types:
            recommendations.append(f"ℹ️  Missing element types in output: {missing_types}")
        
        if not recommendations:
            recommendations.append("✅ Extraction and chunking appear to be working well")
        
        return recommendations


def save_extraction_for_analysis(
    document_id: int,
    document_title: str,
    extracted_json: Dict[str, Any],
    chunks: List[Dict[str, Any]]
) -> None:
    """
    Convenience function to save extraction results.
    
    Call this after successful chunking to analyze results:
    
    Example:
        from app.debug_extraction_analyzer import save_extraction_for_analysis
        save_extraction_for_analysis(doc_id, title, adobe_json, chunks)
    """
    analyzer = ExtractionAnalyzer()
    analyzer.save_extraction_analysis(
        document_id=document_id,
        document_title=document_title,
        extracted_json=extracted_json,
        chunks=chunks
    )
