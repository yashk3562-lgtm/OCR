import asyncio
import mimetypes
import os
import time
import typing
import httpx
from http import HTTPStatus

from ..types import JobStatusResponse

if typing.TYPE_CHECKING:
    from .client import AsyncSpeechToTextJobClient, SpeechToTextJobClient


class AsyncSpeechToTextJob:
    def __init__(self, job_id: str, client: "AsyncSpeechToTextJobClient"):
        """
        Initialize the asynchronous speech-to-text job.

        Parameters
        ----------
        job_id : str
            The unique job identifier returned from a previous job initialization.

        client : AsyncSpeechToTextJobClient
            The async client instance used to create the job.

            !!! important
                This must be the **same client instance** that was used to initialize
                the job originally, as it contains the subscription key and configuration
                required to authenticate and manage the job.

        """
        self._job_id = job_id
        self._client = client

    @property
    def job_id(self) -> str:
        """
        Returns the job ID associated with this job instance.

        Returns
        -------
        str
        """
        return self._job_id

    async def upload_files(
        self, file_paths: typing.Sequence[str], timeout: float = 60.0
    ) -> bool:
        """
        Upload input audio files for the speech-to-text job.

        Parameters
        ----------
        file_paths : Sequence[str]
            List of full paths to local audio files.

        timeout : float, optional
            The maximum time to wait for the upload to complete (in seconds),
            by default 60.0
        Returns
        -------
        bool
            True if all files are uploaded successfully.
        """
        upload_links = await self._client.get_upload_links(
            job_id=self._job_id,
            files=[os.path.basename(p) for p in file_paths],
        )
        client_timeout = httpx.Timeout(timeout=timeout)
        async with httpx.AsyncClient(timeout=client_timeout) as session:
            for path in file_paths:
                file_name = os.path.basename(path)
                url = upload_links.upload_urls[file_name].file_url
                with open(path, "rb") as f:
                    content_type, _ = mimetypes.guess_type(path)
                    if content_type is None:
                        content_type = "audio/wav"
                    response = await session.put(
                        url,
                        content=f.read(),
                        headers={
                            "x-ms-blob-type": "BlockBlob",
                            "Content-Type": content_type,
                        },
                    )
                if (
                    response.status_code > HTTPStatus.IM_USED
                    or response.status_code < HTTPStatus.OK
                ):
                    raise RuntimeError(
                        f"Upload failed for {file_name}: {response.status_code}"
                    )
        return True

    async def wait_until_complete(
        self, poll_interval: int = 5, timeout: int = 600
    ) -> JobStatusResponse:
        """
        Polls job status until it completes or fails.

        Parameters
        ----------
        poll_interval : int, optional
            Time in seconds between polling attempts (default is 5).

        timeout : int, optional
            Maximum time to wait for completion in seconds (default is 600).

        Returns
        -------
        JobStatusResponse
            Final job status.

        Raises
        ------
        TimeoutError
            If the job does not complete within the given timeout.
        """
        start = asyncio.get_event_loop().time()
        while True:
            status = await self.get_status()
            state = status.job_state.lower()
            if state in {"completed", "failed"}:
                return status
            if asyncio.get_event_loop().time() - start > timeout:
                raise TimeoutError(
                    f"Job {self._job_id} did not complete within {timeout} seconds."
                )
            await asyncio.sleep(poll_interval)

    async def get_output_mappings(self) -> typing.List[typing.Dict[str, str]]:
        """
        Get the mapping of input files to their corresponding output files.

        Returns
        -------
        List[Dict[str, str]]
            List of mappings with keys 'input_file' and 'output_file'.
        """
        job_status = await self.get_status()
        return [
            {
                "input_file": detail.inputs[0].file_name,
                "output_file": detail.outputs[0].file_name,
            }
            for detail in (job_status.job_details or [])
            if detail.inputs and detail.outputs and detail.state == "Success"
        ]

    async def get_file_results(
        self,
    ) -> typing.Dict[str, typing.List[typing.Dict[str, typing.Any]]]:
        """
        Get detailed results for each file in the batch job.

        Returns
        -------
        Dict[str, List[Dict[str, Any]]]
            Dictionary with 'successful' and 'failed' keys, each containing a list of file details.
            Each file detail includes:
            - 'file_name': Name of the input file
            - 'status': Status of processing ('Success' or 'Failed')
            - 'error_message': Error message if failed (None if successful)
            - 'output_file': Name of output file if successful (None if failed)
        """
        job_status = await self.get_status()
        results: typing.Dict[str, typing.List[typing.Dict[str, typing.Any]]] = {
            "successful": [],
            "failed": [],
        }

        for detail in job_status.job_details or []:
            # Check for empty lists explicitly
            if not detail.inputs or len(detail.inputs) == 0:
                continue

            try:
                file_info = {
                    "file_name": detail.inputs[0].file_name,
                    "status": detail.state,
                    "error_message": detail.error_message,
                    "output_file": (
                        detail.outputs[0].file_name
                        if detail.outputs and len(detail.outputs) > 0
                        else None
                    ),
                }

                if detail.state == "Success":
                    results["successful"].append(file_info)
                else:
                    results["failed"].append(file_info)
            except (IndexError, AttributeError):
                # Skip malformed job details
                continue

        return results

    async def download_outputs(self, output_dir: str) -> bool:
        """
        Download output files to the specified directory.

        Parameters
        ----------
        output_dir : str
            Local directory where outputs will be saved.

        Returns
        -------
        bool
            True if all files downloaded successfully.

        Raises
        ------
        RuntimeError
            If a file fails to download.
        """
        mappings = await self.get_output_mappings()
        file_names = [m["output_file"] for m in mappings]
        download_links = await self._client.get_download_links(
            job_id=self._job_id, files=file_names
        )

        os.makedirs(output_dir, exist_ok=True)
        async with httpx.AsyncClient() as session:
            for m in mappings:
                url = download_links.download_urls[m["output_file"]].file_url
                response = await session.get(url)
                if (
                    response.status_code > HTTPStatus.IM_USED
                    or response.status_code < HTTPStatus.OK
                ):
                    raise RuntimeError(
                        f"Download failed for {m['output_file']}: {response.status_code}"
                    )
                output_path = os.path.join(output_dir, f"{m['input_file']}.json")
                with open(output_path, "wb") as f:
                    f.write(response.content)
        return True

    async def get_status(self) -> JobStatusResponse:
        """
        Retrieve the current status of the job.

        Returns
        -------
        JobStatusResponse
        """
        return await self._client.get_status(self._job_id)

    async def start(self) -> JobStatusResponse:
        """
        Start the speech-to-text job processing.

        Returns
        -------
        JobStatusResponse
        """
        return await self._client.start(job_id=self._job_id)

    async def exists(self) -> bool:
        """
        Check if the job exists in the system.

        Returns
        -------
        bool
        """
        try:
            await self.get_status()
            return True
        except httpx.HTTPStatusError:
            return False

    async def is_complete(self) -> bool:
        """
        Check if the job is either completed or failed.

        Returns
        -------
        bool
        """
        state = (await self.get_status()).job_state.lower()
        return state in {"completed", "failed"}

    async def is_successful(self) -> bool:
        """
        Check if the job completed successfully.

        Returns
        -------
        bool
        """
        return (await self.get_status()).job_state.lower() == "completed"

    async def is_failed(self) -> bool:
        """
        Check if the job has failed.

        Returns
        -------
        bool
        """
        return (await self.get_status()).job_state.lower() == "failed"


class SpeechToTextJob:
    def __init__(self, job_id: str, client: "SpeechToTextJobClient"):
        """
        Initialize the synchronous speech-to-text job.

        Parameters
        ----------
        job_id : str
            The unique job identifier returned from a previous job initialization.

        client : SpeechToTextJobClient
            The client instance used to create the job.

            !!! important
                This must be the **same client instance** that was used to initialize
                the job originally, as it contains the subscription key and configuration
                required to authenticate and manage the job.

        """
        self._job_id = job_id
        self._client = client

    @property
    def job_id(self) -> str:
        """
        Returns the job ID associated with this job instance.

        Returns
        -------
        str
        """
        return self._job_id

    def upload_files(
        self, file_paths: typing.Sequence[str], timeout: float = 60.0
    ) -> bool:
        """
        Upload input audio files for the speech-to-text job.

        Parameters
        ----------
        file_paths : Sequence[str]
            List of full paths to local audio files.

        timeout : float, optional
            The maximum time to wait for the upload to complete (in seconds),
            by default 60.0
        Returns
        -------
        bool
            True if all files are uploaded successfully.
        """
        upload_links = self._client.get_upload_links(
            job_id=self._job_id, files=[os.path.basename(p) for p in file_paths]
        )
        client_timeout = httpx.Timeout(timeout=timeout)
        with httpx.Client(timeout=client_timeout) as client:
            for path in file_paths:
                file_name = os.path.basename(path)
                url = upload_links.upload_urls[file_name].file_url
                with open(path, "rb") as f:
                    response = client.put(
                        url,
                        content=f,
                        headers={
                            "x-ms-blob-type": "BlockBlob",
                            "Content-Type": "audio/wav",
                        },
                    )
                if (
                    response.status_code > HTTPStatus.IM_USED
                    or response.status_code < HTTPStatus.OK
                ):
                    raise RuntimeError(
                        f"Upload failed for {file_name}: {response.status_code}"
                    )
        return True

    def wait_until_complete(
        self, poll_interval: int = 5, timeout: int = 600
    ) -> JobStatusResponse:
        """
        Polls job status until it completes or fails.

        Parameters
        ----------
        poll_interval : int, optional
            Time in seconds between polling attempts (default is 5).

        timeout : int, optional
            Maximum time to wait for completion in seconds (default is 600).

        Returns
        -------
        JobStatusResponse
            Final job status.

        Raises
        ------
        TimeoutError
            If the job does not complete within the given timeout.
        """
        start = time.monotonic()
        while True:
            status = self.get_status()
            state = status.job_state.lower()
            if state in {"completed", "failed"}:
                return status
            if time.monotonic() - start > timeout:
                raise TimeoutError(
                    f"Job {self._job_id} did not complete within {timeout} seconds."
                )
            time.sleep(poll_interval)

    def get_output_mappings(self) -> typing.List[typing.Dict[str, str]]:
        """
        Get the mapping of input files to their corresponding output files.

        Returns
        -------
        List[Dict[str, str]]
            List of mappings with keys 'input_file' and 'output_file'.
        """
        job_status = self.get_status()
        return [
            {
                "input_file": detail.inputs[0].file_name,
                "output_file": detail.outputs[0].file_name,
            }
            for detail in (job_status.job_details or [])
            if detail.inputs and detail.outputs and detail.state == "Success"
        ]

    def get_file_results(
        self,
    ) -> typing.Dict[str, typing.List[typing.Dict[str, typing.Any]]]:
        """
        Get detailed results for each file in the batch job.

        Returns
        -------
        Dict[str, List[Dict[str, Any]]]
            Dictionary with 'successful' and 'failed' keys, each containing a list of file details.
            Each file detail includes:
            - 'file_name': Name of the input file
            - 'status': Status of processing ('Success' or 'Failed')
            - 'error_message': Error message if failed (None if successful)
            - 'output_file': Name of output file if successful (None if failed)
        """
        job_status = self.get_status()
        results: typing.Dict[str, typing.List[typing.Dict[str, typing.Any]]] = {
            "successful": [],
            "failed": [],
        }

        for detail in job_status.job_details or []:
            # Check for empty lists explicitly
            if not detail.inputs or len(detail.inputs) == 0:
                continue

            try:
                file_info = {
                    "file_name": detail.inputs[0].file_name,
                    "status": detail.state,
                    "error_message": detail.error_message,
                    "output_file": (
                        detail.outputs[0].file_name
                        if detail.outputs and len(detail.outputs) > 0
                        else None
                    ),
                }

                if detail.state == "Success":
                    results["successful"].append(file_info)
                else:
                    results["failed"].append(file_info)
            except (IndexError, AttributeError):
                # Skip malformed job details
                continue

        return results

    def download_outputs(self, output_dir: str) -> bool:
        """
        Download output files to the specified directory.

        Parameters
        ----------
        output_dir : str
            Local directory where outputs will be saved.

        Returns
        -------
        bool
            True if all files downloaded successfully.

        Raises
        ------
        RuntimeError
            If a file fails to download.
        """
        mappings = self.get_output_mappings()
        file_names = [m["output_file"] for m in mappings]
        download_links = self._client.get_download_links(
            job_id=self._job_id, files=file_names
        )

        os.makedirs(output_dir, exist_ok=True)
        with httpx.Client() as client:
            for m in mappings:
                url = download_links.download_urls[m["output_file"]].file_url
                response = client.get(url)
                if (
                    response.status_code > HTTPStatus.IM_USED
                    or response.status_code < HTTPStatus.OK
                ):
                    raise RuntimeError(
                        f"Download failed for {m['output_file']}: {response.status_code}"
                    )
                output_path = os.path.join(output_dir, f"{m['input_file']}.json")
                with open(output_path, "wb") as f:
                    f.write(response.content)
        return True

    def get_status(self) -> JobStatusResponse:
        """
        Retrieve the current status of the job.

        Returns
        -------
        JobStatusResponse
        """
        return self._client.get_status(self._job_id)

    def start(self) -> JobStatusResponse:
        """
        Start the speech-to-text job processing.

        Returns
        -------
        JobStatusResponse
        """
        return self._client.start(job_id=self._job_id)

    def exists(self) -> bool:
        """
        Check if the job exists in the system.

        Returns
        -------
        bool
        """
        try:
            self.get_status()
            return True
        except httpx.HTTPStatusError:
            return False

    def is_complete(self) -> bool:
        """
        Check if the job is either completed or failed.

        Returns
        -------
        bool
        """
        return self.get_status().job_state.lower() in {"completed", "failed"}

    def is_successful(self) -> bool:
        """
        Check if the job completed successfully.

        Returns
        -------
        bool
        """
        return self.get_status().job_state.lower() == "completed"

    def is_failed(self) -> bool:
        """
        Check if the job has failed.

        Returns
        -------
        bool
        """
        return self.get_status().job_state.lower() == "failed"
